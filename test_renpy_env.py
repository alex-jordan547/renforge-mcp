#!/usr/bin/env python3
"""
Minimal test to check if Ren'Py 8.5.3 can run in this environment.
"""

import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, '/workspace/src')

from renforge.sdk import get_or_install_sdk
from renforge.project import RenpyProject

def test_renpy_available():
    """Test if Ren'Py 8.5.3 can be installed and run."""
    print("="* 60)
    print("Testing Ren'Py 8.5.3 availability")
    print("="* 60)
    
    # Use clean fixture
    fixture_path = Path("/workspace/tests/fixtures/say_what_clean")
    print(f"\n1. Using fixture: {fixture_path}")
    print(f"   Exists: {fixture_path.exists()}")
    
    try:
        print("\n2. Installing/getting Ren'Py 8.5.3 SDK...")
        sdk = get_or_install_sdk("8.5.3", project_root=fixture_path)
        print(f"   SDK path: {sdk.root}")
        print(f"   SDK version: {sdk.version}")
        print(f"   SDK exists: {sdk.root.exists()}")
        
        print("\n3. Creating RenpyProject...")
        project = RenpyProject(fixture_path)
        print(f"   Project root: {project.root}")
        print(f"   Project exists: {project.root.exists()}")
        
        print("\n4. Checking if we can launch Ren'Py...")
        print(f"   SDK renpy.sh: {sdk.root / 'renpy.sh'}")
        print(f"   Exists: {(sdk.root / 'renpy.sh').exists()}")
        
        print("\n✅ Ren'Py 8.5.3 SDK is available!")
        print("\nNext step: Try to actually launch with bridge...")
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print(f"   Type: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_renpy_available()
    sys.exit(0 if success else 1)
