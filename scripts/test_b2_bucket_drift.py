"""Tests for scripts/b2-bucket-drift.py (diff logic — no network)."""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "b2_bucket_drift", pathlib.Path(__file__).parent / "b2-bucket-drift.py"
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def clean_bucket() -> dict:
    return {
        "bucketName": "weisssrv-backup",
        "bucketType": "allPrivate",
        "defaultServerSideEncryption": {
            "isClientAuthorizedToRead": True,
            "value": {"mode": "SSE-B2", "algorithm": "AES256"},
        },
        "lifecycleRules": [
            {
                "fileNamePrefix": "",
                "daysFromHidingToDeleting": 30,
                "daysFromUploadingToHiding": None,
            }
        ],
        "fileLockConfiguration": {
            "isClientAuthorizedToRead": True,
            "value": {
                "isFileLockEnabled": False,
                "defaultRetention": {"mode": None, "period": None},
            },
        },
    }


class DiffBucketTests(unittest.TestCase):
    def test_clean_bucket_has_no_drift(self):
        self.assertEqual(mod.diff_bucket(clean_bucket()), [])

    def test_wrong_bucket_type_drifts(self):
        b = clean_bucket()
        b["bucketType"] = "allPublic"
        self.assertTrue(any("bucketType" in d for d in mod.diff_bucket(b)))

    def test_missing_sse_drifts(self):
        b = clean_bucket()
        b["defaultServerSideEncryption"] = {
            "isClientAuthorizedToRead": True,
            "value": {"mode": None, "algorithm": None},
        }
        self.assertTrue(any("SSE" in d for d in mod.diff_bucket(b)))

    def test_missing_lifecycle_rule_drifts(self):
        b = clean_bucket()
        b["lifecycleRules"] = []
        self.assertTrue(any("lifecycleRules" in d for d in mod.diff_bucket(b)))

    def test_extra_lifecycle_rule_drifts(self):
        b = clean_bucket()
        b["lifecycleRules"].append(
            {"fileNamePrefix": "restic/", "daysFromUploadingToHiding": 1}
        )
        self.assertTrue(any("lifecycleRules" in d for d in mod.diff_bucket(b)))

    def test_default_retention_set_drifts(self):
        b = clean_bucket()
        b["fileLockConfiguration"]["value"]["defaultRetention"] = {
            "mode": "governance",
            "period": {"duration": 7, "unit": "days"},
        }
        self.assertTrue(any("defaultRetention" in d for d in mod.diff_bucket(b)))

    def test_unreadable_sections_surface_as_drift(self):
        # The terraform provider nulled its whole read on the unreadable
        # fileLock section — this check must instead NAME the capability gap.
        b = clean_bucket()
        b["fileLockConfiguration"] = {"isClientAuthorizedToRead": False, "value": None}
        b["defaultServerSideEncryption"] = {
            "isClientAuthorizedToRead": False,
            "value": None,
        }
        drift = mod.diff_bucket(b)
        self.assertTrue(any("fileLock" in d and "capabilities" in d for d in drift))
        self.assertTrue(any("SSE" in d and "capabilities" in d for d in drift))


if __name__ == "__main__":
    unittest.main()
