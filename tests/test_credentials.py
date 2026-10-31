import os
from pathlib import Path
import tempfile
import unittest

from backend.credentials import CredentialError, load_deepseek_key, save_deepseek_key


@unittest.skipUnless(os.name == "nt", "Windows DPAPI")
class CredentialTests(unittest.TestCase):
    def test_encrypted_key_roundtrip_and_clear(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "key.bin"
            self.assertEqual(load_deepseek_key(path), "")
            save_deepseek_key(path, "dummy-test-key-only")
            self.assertNotIn(b"dummy-test-key-only", path.read_bytes())
            self.assertEqual(load_deepseek_key(path), "dummy-test-key-only")
            save_deepseek_key(path, "")
            self.assertFalse(path.exists())

    def test_corrupt_ciphertext_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "key.bin"
            path.write_bytes(b"invalid ciphertext")
            with self.assertRaises(CredentialError):
                load_deepseek_key(path)
            self.assertEqual(path.read_bytes(), b"invalid ciphertext")


if __name__ == "__main__":
    unittest.main()
