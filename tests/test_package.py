import unittest

import agent


class PackageTests(unittest.TestCase):
    def test_package_exposes_version(self):
        self.assertRegex(agent.__version__, r"^\d+\.\d+\.\d+$")
