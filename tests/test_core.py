"""Offline tests: python -m unittest discover tests"""
import tempfile
import unittest
from pathlib import Path

from jobbot.c2c import c2c_status, excluded_reason, find_contact_email
from jobbot.db import Tracker, fingerprint, normalize_url
from jobbot.models import Job
from jobbot.resume import Education, Experience, Resume, SkillGroup, render_docx
from jobbot.tailor import ExperienceRewrite, Tailor, TailorOutput


def job(**kw):
    base = dict(source="dice", source_id="1", url="https://www.dice.com/job-detail/1",
                title="Java Developer", company="Acme Inc", location="Dallas, TX",
                description="Build services")
    base.update(kw)
    return Job(**base)


MASTER = Resume(
    name="Test Person", headline="Senior Java Developer", email="t@example.com", phone="1",
    location="Dallas, TX", links=[], summary="Java developer with 10 years of experience.",
    skills=[SkillGroup(category="Backend", items=["Java", "Spring Boot", "Kafka"])],
    experience=[Experience(company="Bank Co", title="Java Developer", location="Dallas, TX",
                           start="2020", end="Present",
                           bullets=["Built Spring Boot microservices", "Cut latency by 30%",
                                    "Integrated Kafka streams"])],
    education=[Education(degree="BS CS", school="State U", year="2012")],
    certifications=[],
)


class C2CTests(unittest.TestCase):
    def test_allowed(self):
        self.assertEqual(c2c_status(job(description="Contract, C2C is fine"))[0], "allowed")
        self.assertEqual(c2c_status(job(employment_type="Contract, Third Party"))[0], "allowed")
        self.assertEqual(c2c_status(job(description="open to corp-to-corp"))[0], "allowed")

    def test_not_allowed_wins(self):
        for text in ["W2 only, no C2C", "Only W2 candidates", "No third party please",
                     "C2C not allowed", "Java Architect @w2 only", "not open to C2C"]:
            self.assertEqual(c2c_status(job(description=text))[0], "not_allowed", text)

    def test_weak_negative_only_without_c2c(self):
        self.assertEqual(c2c_status(job(title="Direct Client :: W2 position"))[0], "not_allowed")
        self.assertEqual(c2c_status(job(description="W2 or C2C both fine"))[0], "allowed")

    def test_unclear(self):
        self.assertEqual(c2c_status(job(description="Great team"))[0], "unclear")
        self.assertEqual(c2c_status(job(source="corptocorp", description="x"))[0], "allowed")

    def test_exclusions(self):
        cfg = {"filters": {"exclude_keywords": ["security clearance"],
                           "exclude_title_keywords": ["hotlist"]}}
        self.assertTrue(excluded_reason(job(description="Needs Security Clearance"), cfg))
        self.assertTrue(excluded_reason(job(title="Hotlist | Java"), cfg))
        self.assertFalse(excluded_reason(job(), cfg))

    def test_email(self):
        self.assertEqual(find_contact_email("send to Ravi.K@vendor.com."), "ravi.k@vendor.com")
        self.assertEqual(find_contact_email("apply at jobs@dice.com"), "")


class DedupeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Tracker(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.t.conn.close()
        self.tmp.cleanup()

    def apply(self, j):
        row = self.t.record_seen(j)
        self.t.set_status(row["id"], "applied")

    def test_same_id_same_url_cross_board(self):
        a = job()
        self.apply(a)
        self.assertIsNotNone(self.t.find_prior_application(a, 45))
        # Same role reposted on LinkedIn by the same vendor, same city.
        b = job(source="linkedin", source_id="99", url="https://linkedin.com/jobs/view/99/",
                title="Java Developer - C2C (Remote)", company="ACME, Inc.", location="Dallas, Texas")
        self.assertIsNotNone(self.t.find_prior_application(b, 45))
        # Same URL with tracking params.
        c = job(source="dice", source_id="2", url="https://www.dice.com/job-detail/1/?utm=x")
        self.assertIsNotNone(self.t.find_prior_application(c, 45))

    def test_different_city_or_title_is_new(self):
        self.apply(job())
        self.assertIsNone(self.t.find_prior_application(
            job(source_id="3", url="u3", location="Austin, TX"), 45))
        self.assertIsNone(self.t.find_prior_application(
            job(source_id="4", url="u4", title="Python Developer"), 45))

    def test_not_applied_is_not_duplicate(self):
        self.t.record_seen(job())
        self.assertIsNone(self.t.find_prior_application(job(source_id="5", url="u5"), 45))

    def test_manual_mark(self):
        self.t.mark_applied_manually("https://www.indeed.com/viewjob?jk=abc&from=x")
        j = job(source="indeed", source_id="abc", url="https://www.indeed.com/viewjob?jk=abc")
        self.assertIsNotNone(self.t.find_prior_application(j, 45))

    def test_helpers(self):
        self.assertEqual(normalize_url("https://WWW.Dice.com/job-detail/X/?a=1#f"), "dice.com/job-detail/x")
        self.assertEqual(fingerprint("Acme Inc", "Sr Java Developer", "Dallas"),
                         fingerprint("ACME", "Sr. Java Developer || C2C", "Dallas, TX"))
        self.assertNotEqual(fingerprint("Acme", "Java Developer"), fingerprint("Acme", "Senior Java Developer"))


class TailorGuardTests(unittest.TestCase):
    def out(self, **kw):
        base = dict(match_score=80, c2c_assessment="allowed", blockers=[],
                    headline="Java / Kafka Engineer", summary="Java developer with 10 years.",
                    skills=[SkillGroup(category="Core", items=["Kafka", "Java", "Rust"])],
                    experience=[ExperienceRewrite(index=0, bullets=[
                        "Integrated Kafka event streams", "Built Spring Boot microservices",
                        "Cut latency by 30%"])],
                    changes=[], recruiter_note="Hi")
        base.update(kw)
        return TailorOutput(**base)

    def test_invented_skill_dropped_and_bullets_reordered(self):
        r = Tailor._apply_guarded(MASTER, self.out())
        items = r.resume.skills[0].items
        self.assertEqual(items, ["Kafka", "Java"])
        self.assertEqual(r.resume.experience[0].bullets[0], "Integrated Kafka event streams")
        self.assertEqual(r.resume.experience[0].company, "Bank Co")

    def test_invented_numbers_reverted(self):
        o = self.out(summary="Java developer with 15 years.", experience=[
            ExperienceRewrite(index=0, bullets=["Cut latency by 60%", "Built services"])])
        r = Tailor._apply_guarded(MASTER, o)
        self.assertEqual(r.resume.summary, MASTER.summary)
        self.assertEqual(r.resume.experience[0].bullets, MASTER.experience[0].bullets)
        self.assertTrue(r.guard_notes)

    def test_render_docx(self):
        r = Tailor._apply_guarded(MASTER, self.out())
        path = render_docx(r.resume, "Acme Inc", "Java Developer", "test0")
        self.assertTrue(path.exists() and path.stat().st_size > 1000)
        path.unlink()


if __name__ == "__main__":
    unittest.main()
