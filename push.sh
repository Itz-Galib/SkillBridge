#!/usr/bin/env bash
# Push this project to GitHub. Requires git credentials (HTTPS token or SSH key) on your machine.
set -e
git remote get-url origin >/dev/null 2>&1 || git remote add origin https://github.com/bappyBDN/SkillBridge.git
git branch -M main
git push -u origin main
