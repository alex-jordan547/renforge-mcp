#!/usr/bin/env python3
"""
Live test for #81 say.what style position with Ren'Py 8.5.3.
This is the REAL execution test - not a pytest skip stub.
"""

import sys
import os
import shutil
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, '/workspace/src')
sys.path.insert(0, '/workspace')

def inject_renforge_editor(project_root: Path) -> None:
    """Inject RenForge editor bridge files into game."""
    bridge_src = Path("/workspace/src/renforge/bridge")
    game_dir = project_root / "game"
    
    # Copy editor files
    files_to_copy = [
        "editor.rpy",
        "editor_assets",
    ]
    
    for name in files_to_copy:
        src = bridge_src / name
        dst = game_dir / name
        
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            print(f"   Copied {name}/ directory")
        elif src.is_file():
            shutil.copy2(src, dst)
            print(f"   Copied {name}")


def prepare_fixture() -> Path:
    """Prepare a clean copy of the fixture for testing."""
    print("="* 60)
    print("Preparing clean fixture for #81 live test")
    print("="* 60)
    
    # Use temp directory
    import tempfile
    temp_dir = Path(tempfile.mkdtemp(prefix="say_what_live_"))
    fixture_copy = temp_dir / "say_what_clean"
    
    source = Path("/workspace/tests/fixtures/say_what_clean")
    print(f"\n1. Copying fixture from {source}")
    print(f"   To: {fixture_copy}")
    
    shutil.copytree(source, fixture_copy)
    
    print("\n2. Injecting RenForge editor...")
    inject_renforge_editor(fixture_copy)
    
    # CRITICAL: Clear all Ren'Py cache to prevent stale .rpyc issues
    print("\n3. Clearing Ren'Py cache...")
    cache_dir = fixture_copy / ".renpy" / "cache"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        print(f"   ✓ Cleared {cache_dir}")
    else:
        print(f"   ✓ No cache dir (fresh fixture)")
    
    # Also remove any .rpyc files in game/
    game_dir = fixture_copy / "game"
    rpyc_files = list(game_dir.glob("**/*.rpyc"))
    for rpyc in rpyc_files:
        rpyc.unlink()
    if rpyc_files:
        print(f"   ✓ Removed {len(rpyc_files)} stale .rpyc file(s)")
    else:
        print("   ✓ No stale .rpyc files")
    
    print("\n4. Verifying fixture structure...")
    required_files = ["gui.rpy", "screens.rpy", "script.rpy", "options.rpy", "editor.rpy"]
    for file in required_files:
        path = game_dir / file
        exists = "✓" if path.exists() else "✗"
        print(f"   {exists} {file}")
    
    return fixture_copy


def run_say_what_live_test(fixture_path: Path) -> dict:
    """
    Run the actual live test scenario for #81.
    
    Returns a report dict with pass/fail for each step.
    """
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk
    from renforge.editor_say_what_position_runner import run_editor_say_what_style_position_live_scenario
    
    print("\n" + "="* 60)
    print("Running #81 Live Test with Ren'Py 8.5.3")
    print("="* 60)
    
    report = {}
    
    try:
        print("\n1. Installing Ren'Py 8.5.3 SDK...")
        sdk = get_or_install_sdk("8.5.3", project_root=fixture_path)
        print(f"   ✓ SDK at {sdk.root}")
        report["sdk_installed"] = True
        
        print("\n2. Launching Ren'Py with bridge...")
        screens_path = fixture_path / "game" / "screens.rpy"
        gui_path = fixture_path / "game" / "gui.rpy"
        
        with launch_with_bridge(
            sdk,
            RenpyProject(fixture_path),
            startup_timeout=120,
            editor=True,
        ) as session:
            print("   ✓ Ren'Py launched")
            report["renpy_launched"] = True
            
            print("\n3. Showing say screen (before opening editor)...")
            # Show say screen BEFORE opening editor so coordinator can analyze it
            session.client.eval_expr(
                'renpy.show_screen("say", who=None, what="Test dialogue for RenForge #81", _layer="screens")'
            )
            # Verify say screen is active
            say_active = session.client.inspect_screen("say").get("active")
            if not say_active:
                report["verdict"] = "fail"
                report["error"] = "say screen failed to show"
                return report
            print("   ✓ Say screen active")
            
            print("\n4. Opening editor...")
            # Wait for editor launcher
            for _ in range(40):
                if session.client.inspect_screen("_renforge_editor_launcher").get("active") is True:
                    break
                time.sleep(0.2)
            else:
                report["verdict"] = "fail"
                report["error"] = "editor launcher never became active"
                return report
            
            # Click RF button
            session.client.click_element(
                text="RF",
                exact=True,
                screen="_renforge_editor_launcher",
            )
            
            # Wait for editor overlay
            for _ in range(40):
                if session.client.inspect_screen("_renforge_editor_overlay").get("active") is True:
                    break
                time.sleep(0.05)
            else:
                report["verdict"] = "fail"
                report["error"] = "editor overlay never became active"
                return report
            
            print("   ✓ Editor opened")
            report["editor_opened"] = True
            
            print("\n5. Running full say.what style position scenario...")
            scenario_report = run_editor_say_what_style_position_live_scenario(
                session.client,
                fixture_path=screens_path,
                gui_path=gui_path,
            )
            
            report.update(scenario_report)
            
        print("\n" + "="* 60)
        print("Live Test Complete")
        print("="* 60)
        
        return report
        
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        report["verdict"] = "fail"
        report["exception"] = str(e)
        report["exception_type"] = type(e).__name__
        return report


def print_report(report: dict) -> None:
    """Print formatted test report."""
    print("\n" + "="* 60)
    print("TEST REPORT")
    print("="* 60)
    
    verdict = report.get("verdict", "unknown")
    print(f"\nOverall Verdict: {verdict.upper()}")
    
    print("\nSteps completed:")
    for key in ["sdk_installed", "renpy_launched", "editor_opened", "move_unlocked"]:
        if key in report:
            status = "✓" if report[key] else "✗"
            print(f"  {status} {key}: {report[key]}")
    
    print("\nDetailed results:")
    for key, value in sorted(report.items()):
        if key not in ["sdk_installed", "renpy_launched", "editor_opened"]:
            print(f"  {key}: {value}")
    
    if verdict == "pass":
        print("\n✅ ALL ACCEPTANCE CRITERIA MET")
        print("   - say.what unlocked with style_gui_dialogue")
        print("   - Preview without TypeError/dialogue advance")
        print("   - Delta math correct (not absolute screen coords)")
        print("   - Undo byte-identical")
    else:
        print(f"\n❌ LIVE TEST FAILED")
        if "error" in report:
            print(f"   Error: {report['error']}")


def main():
    """Main test entry point."""
    try:
        # Prepare fixture
        fixture_path = prepare_fixture()
        
        # Run test
        report = run_say_what_live_test(fixture_path)
        
        # Print report
        print_report(report)
        
        # Clean up
        print(f"\nFixture path: {fixture_path}")
        print("(Temp directory will be cleaned up on next reboot)")
        
        # Return exit code based on verdict
        verdict = report.get("verdict", "fail")
        return 0 if verdict == "pass" else 1
        
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        return 130
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
