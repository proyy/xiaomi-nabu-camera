#!/usr/bin/python3
# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
# Noncommercial use only; see LICENSE. Third-party components retain their licenses.
import os
from nabu_camera import ROOT, environment, platform_check
platform_check()
binary=str(ROOT/'camera-bridge')
os.execve(binary,[binary],environment())
