#!/usr/bin/python3
# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
# Noncommercial use only; see LICENSE. Third-party components retain their licenses.
"""Check that failed capture cannot leave the physical LED enabled."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import flash_control as flash


class FlashCleanup(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.led=Path(self.directory.name)
        for name in ['brightness','flash_strobe','flash_timeout','flash_brightness','flash_fault']:
            (self.led/name).write_text('0')
        self.redirect=patch.object(flash,'LED',self.led)
        self.redirect.start()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.redirect.stop)

    def test_torch_can_be_cleared(self):
        flash.torch(True)
        self.assertEqual((self.led/'brightness').read_text(),'128')
        flash.off()
        self.assertEqual((self.led/'brightness').read_text(),'0')
        self.assertEqual((self.led/'flash_strobe').read_text(),'0')

    def test_capture_failure_clears_preflash(self):
        class FailedCamera:
            side='rear'; mtime=1
            def snapshot(self,previous):
                raise RuntimeError('camera disconnected')
        with self.assertRaisesRegex(RuntimeError,'disconnected'):
            flash.photo(FailedCamera())
        self.assertEqual((self.led/'brightness').read_text(),'0')
        self.assertEqual((self.led/'flash_strobe').read_text(),'0')

    def test_front_cannot_trigger_rear_flash(self):
        class FrontCamera:
            side='front'
        with self.assertRaises(RuntimeError):
            flash.photo(FrontCamera())
        self.assertEqual((self.led/'brightness').read_text(),'0')
        self.assertEqual((self.led/'flash_strobe').read_text(),'0')


if __name__=='__main__':
    unittest.main()
