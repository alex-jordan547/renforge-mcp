#!/usr/bin/env python3
"""Quick boot test for say_what_clean fixture."""

import sys
sys.path.insert(0, '/workspace/src')

from pathlib import Path
from renforge.sdk import get_or_install_sdk
from renforge.project import RenpyProject
from renforge.bridge.launcher import launch_with_bridge

def test_fixture_boots():
    fixture = Path("/workspace/tests/fixtures/say_what_clean")
    print(f"Testing fixture boot: {fixture}")
    
    sdk = get_or_install_sdk("8.5.3", project_root=fixture)
    print(f"✓ SDK ready")
    
    try:
        with launch_with_bridge(
            sdk,
            RenpyProject(fixture),
            startup_timeout=120,
            editor=False,  # Just boot, no editor yet
        ) as session:
            print("✓ Ren'Py launched successfully!")
            
            # Check if say screen is available
            has_say = session.client.eval_expr('renpy.has_screen("say")')
            print(f"✓ screen say available: {has_say}")
            
            return True
    except Exception as e:
        print(f"✗ Boot failed: {e}")
        return False

if __name__ == "__main__":
    success = test_fixture_boots()
    sys.exit(0 if success else 1)
