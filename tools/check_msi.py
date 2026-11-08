"""Decompile the MSI, verify every installed path/byte, and smoke-test its extracted app."""

import hashlib
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET

from build_msi import ROOT, UPGRADE_CODE, VERSION, WIX_NAMESPACE, payload, toolchain


def check():
    command, _ = toolchain()
    msi = ROOT / "dist" / f"uBridge-{VERSION}-windows-x64.msi"
    work = ROOT / ".cache/msi-check"
    work.mkdir(parents=True, exist_ok=True)
    source = work / "decompiled.wxs"
    extracted = work / "extracted"
    subprocess.run(
        [*command, "msi", "decompile", str(msi), "-x", str(extracted), "-o", str(source)],
        check=True,
        cwd=ROOT,
    )
    ns = {"w": WIX_NAMESPACE}
    document = ET.parse(source)
    package = document.find("w:Package", ns)
    assert package.attrib["Name"] == "uBridge"
    assert package.attrib["Version"] == VERSION
    assert package.attrib["Scope"] == "perUser"
    assert package.attrib["UpgradeCode"] == UPGRADE_CODE
    install = package.find(".//w:Directory[@Id='INSTALLFOLDER']", ns)
    assert install.attrib["Name"] == "uBridge"
    local = package.find("w:StandardDirectory[@Id='LocalAppDataFolder']/w:Directory", ns)
    assert local.attrib["Name"] == "Programs" and install in list(local)
    shortcuts = package.findall(".//w:Shortcut", ns)
    assert any(
        s.attrib.get("Target") == "[INSTALLFOLDER]uBridge.exe"
        and s.attrib.get("Directory") == "uBridgeMenu"
        for s in shortcuts
    )
    dialogs = {d.attrib["Id"] for d in package.findall(".//w:Dialog", ns)}
    assert {"WelcomeEulaDlg", "ProgressDlg", "ExitDialog"} <= dialogs
    launch = package.find("w:CustomAction[@Id='RunuBridge']", ns)
    assert launch is not None and launch.attrib["ExeCommand"] == "--configure"
    checkbox = package.find("w:Property[@Id='WIXUI_EXITDIALOGOPTIONALCHECKBOXTEXT']", ns)
    assert checkbox is not None and checkbox.attrib["Value"] == "Run uBridge"
    assert any(
        event.attrib.get("Value") == "RunuBridge"
        and "NOT Installed" in event.attrib.get("Condition", "")
        for event in package.findall(".//w:Publish", ns)
    )
    expected = {relative: path for path, relative in payload()}
    installed = {}

    def walk(directory, prefix=""):
        for component in directory.findall("w:Component", ns):
            assert component.attrib["Bitness"] == "always64"
            assert component.find("w:RegistryValue[@Root='HKCU'][@KeyPath='yes']", ns) is not None
            for file in component.findall("w:File", ns):
                relative = prefix + file.attrib["Name"]
                assert relative not in installed
                installed[relative] = extracted / "File" / file.attrib["Id"]
        for child in directory.findall("w:Directory", ns):
            walk(child, prefix + child.attrib["Name"] + "/")

    walk(install)
    assert installed.keys() == expected.keys(), (
        "MSI installed file paths differ from source payload"
    )
    app = work / "app"
    for relative, file in installed.items():
        assert (
            hashlib.sha256(file.read_bytes()).digest()
            == hashlib.sha256(expected[relative].read_bytes()).digest()
        ), relative
        destination = app / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, destination)
    diagnostics = work / "diagnostics.json"
    subprocess.run(
        [
            str(app / "uBridge.exe"),
            "--config",
            str(work / "profile/config.json"),
            "--diagnostics",
            str(diagnostics),
        ],
        check=True,
        cwd=app,
        timeout=30,
    )
    report = json.loads(diagnostics.read_text(encoding="utf-8"))
    assert report["version"] == VERSION and all(
        value == "ok" for value in report["imports"].values()
    )
    print(
        "PASS: per-user x64 MSI, upgrade identity, wizard, shortcuts, "
        f"{len(installed)} file paths/hashes, extracted app diagnostics"
    )


if __name__ == "__main__":
    check()
