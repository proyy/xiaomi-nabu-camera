#!/bin/sh
# SPDX-License-Identifier: LicenseRef-Nabu-Camera-NonCommercial-1.0
# Copyright (c) 2026 zouhong <admin@proyy.com>
set -eu
cd "$(dirname "$0")/.."
version=${1:-1.2.0-3}
python3 packaging/build_deb.py --version "$version"
mkdir -p dist
dpkg-deb --root-owner-group -Zxz -z6 --build "build/nabu-camera_${version}_arm64" "dist/nabu-camera_${version}_arm64.deb"
