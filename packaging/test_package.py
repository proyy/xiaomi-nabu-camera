#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
# Noncommercial use only; see LICENSE. Third-party components retain their licenses.
"""Boot integration regression tests; no host boot files are accessed or changed."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('support',Path(__file__).with_name('install_support.py'))
support=importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
BASE='''boot.default = "linux"
boot.configs.linux.extra.kernel = "@part_esp:\\\\vmlinuz-6.1.10-nabu"
boot.configs.linux.extra.dtb = "@part_esp:\\\\dtb-6.1.10-nabu"
boot.configs.linux.extra.cmdline = "root=PARTLABEL=linux loglevel=3 fbcon=rotate:1"
boot.configs.android.mode = 10
'''

class BootIntegration(unittest.TestCase):
    def test_original_linux_updated_only_in_two_pointers(self):
        expected=BASE.replace('vmlinuz-6.1.10-nabu','vmlinuz-6.1.10-nabu-camera3').replace('dtb-6.1.10-nabu','dtb-6.1.10-nabu-camera3')
        self.assertEqual(support.configure_boot_text(BASE),expected)
    def test_reconfigure_is_idempotent(self):
        once=support.configure_boot_text(BASE)
        self.assertEqual(support.configure_boot_text(once),once)
    def test_restore_round_trip(self):
        self.assertEqual(support.configure_boot_text(support.configure_boot_text(BASE),restore=True),BASE)
    def test_existing_test_default_returns_to_original_linux(self):
        text=BASE.replace('boot.default = "linux"','boot.default = "camera_test3"')
        self.assertIn('boot.default = "linux"',support.configure_boot_text(text))
    def test_install_selects_original_linux(self):
        text=BASE.replace('boot.default = "linux"','boot.default = "android"')
        self.assertIn('boot.default = "linux"',support.configure_boot_text(text))
    def test_missing_or_duplicate_key_rejected(self):
        with self.assertRaises(RuntimeError):
            support.configure_boot_text(BASE.replace('boot.configs.linux.extra.dtb','broken'))
        with self.assertRaises(RuntimeError):
            support.configure_boot_text(BASE+BASE.splitlines()[1]+'\n')
    def test_previous_candidate_entry_migrated(self):
        text=BASE.replace('boot.default = "linux"','boot.default = "nabu_camera"')+'boot.configs.nabu_camera.mode = 8\n'
        result=support.configure_boot_text(text)
        self.assertNotIn('nabu_camera',result)
        self.assertIn('boot.default = "linux"',result)
    def test_foreign_arch_rejected_before_filesystem(self):
        with patch.object(support.os,'uname') as uname:
            uname.return_value.machine='x86_64'
            with self.assertRaisesRegex(RuntimeError,'ARM64'):
                support.platform_check()

if __name__=='__main__':
    unittest.main()
