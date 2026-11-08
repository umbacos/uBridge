"""Build the per-user x64 uBridge MSI with a pinned, workspace-local WiX toolchain."""

import hashlib
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
WIX_VERSION = "5.0.2"
WIX_NAMESPACE = "http://wixtoolset.org/schemas/v4/wxs"
UI_NAMESPACE = "http://wixtoolset.org/schemas/v4/wxs/ui"
UPGRADE_CODE = "{0C76C547-A4B2-574C-8452-AB878839577C}"
COMPONENT_NAMESPACE = uuid.UUID("f9a6f704-756d-44a1-a2fe-17bbb429aa72")
PACKAGES = {
    "wix": "f30ef0c74e2a986126539c5780be93ac24e8136eaf723b1937b26272703ae173",
    "wixtoolset.ui.wixext": "5ef2c707614b9f70b6bbadd2d4abcb4124efee215e9b16bfbc80113079a604c7",
}


def toolchain():
    if sys.platform != "win32" or not shutil.which("dotnet"):
        raise SystemExit("Build the MSI on Windows with the .NET 8 runtime installed.")
    base = ROOT / ".cache/msi-toolchain"
    for name, digest in PACKAGES.items():
        archive = base / f"{name}.{WIX_VERSION}.nupkg"
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.is_file():
            url = f"https://api.nuget.org/v3-flatcontainer/{name}/{WIX_VERSION}/{archive.name}"
            with urllib.request.urlopen(url, timeout=60) as response:
                archive.write_bytes(response.read())
        if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
            raise SystemExit(f"Unexpected SHA256 for {archive.name}; remove it and retry")
        folder = base / name
        if not (folder / ".verified").is_file():
            with ZipFile(archive) as z:
                z.extractall(folder)
            (folder / ".verified").write_text(digest, encoding="ascii")
    command = ["dotnet", "--roll-forward", "Major", str(base / "wix/tools/net6.0/any/wix.dll")]
    extension = base / "wixtoolset.ui.wixext/wixext5/WixToolset.UI.wixext.dll"
    return command, extension


def identifier(prefix, value):
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def payload():
    portable = ROOT / ".cache/portable/uBridge"
    if not (portable / "uBridge.exe").is_file():
        raise SystemExit("Run tools/build_bridge.py before building the MSI")
    entries = [
        (p, p.relative_to(portable).as_posix()) for p in sorted(portable.rglob("*")) if p.is_file()
    ]
    for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        entries.append((ROOT / name, name))
    for folder in ("docs", "examples"):
        entries.extend(
            (p, p.relative_to(ROOT).as_posix())
            for p in sorted((ROOT / folder).rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts
        )
    entries.extend(
        (ROOT / "firmware" / name, "firmware/" + name) for name in ("tjc_hmi.tft", "build.json")
    )
    entries.append((ROOT / "licenses/WiX-MS-RL.txt", "licenses/WiX-MS-RL.txt"))
    assert len({name for _, name in entries}) == len(entries)
    return entries


def author(entries, work):
    ET.register_namespace("", WIX_NAMESPACE)
    ET.register_namespace("ui", UI_NAMESPACE)

    def add(parent, tag, **attrs):
        return ET.SubElement(parent, f"{{{WIX_NAMESPACE}}}{tag}", attrs)

    root = ET.Element(f"{{{WIX_NAMESPACE}}}Wix")
    package = add(
        root,
        "Package",
        Name="uBridge",
        Manufacturer="uBridge project",
        Version=VERSION,
        UpgradeCode=UPGRADE_CODE,
        Scope="perUser",
        Language="1033",
        InstallerVersion="500",
    )
    add(
        package,
        "MajorUpgrade",
        AllowSameVersionUpgrades="yes",
        DowngradeErrorMessage="A newer version of uBridge is already installed.",
    )
    add(package, "MediaTemplate", EmbedCab="yes", CompressionLevel="high")
    add(
        package, "Icon", Id="uBridgeIcon", SourceFile=str(ROOT / ".cache/bridge-staging/bridge.ico")
    )
    add(package, "Property", Id="ARPPRODUCTICON", Value="uBridgeIcon")
    local = add(package, "StandardDirectory", Id="LocalAppDataFolder")
    programs = add(local, "Directory", Id="LocalPrograms", Name="Programs")
    install = add(programs, "Directory", Id="INSTALLFOLDER", Name="uBridge")
    menu = add(package, "StandardDirectory", Id="ProgramMenuFolder")
    add(menu, "Directory", Id="uBridgeMenu", Name="uBridge")
    directories = {"": install}
    feature = add(package, "Feature", Id="uBridgeApplication", Title="uBridge", Level="1")
    for source, relative in entries:
        parts = Path(relative).parts
        parent = ""
        for part in parts[:-1]:
            path = f"{parent}/{part}".strip("/")
            if path not in directories:
                directories[path] = add(
                    directories[parent], "Directory", Id=identifier("D", path), Name=part
                )
            parent = path
        component_id = identifier("C", relative)
        component = add(
            directories[parent],
            "Component",
            Id=component_id,
            Guid="{" + str(uuid.uuid5(COMPONENT_NAMESPACE, relative)).upper() + "}",
            Bitness="always64",
        )
        file_id = identifier("F", relative)
        add(component, "File", Id=file_id, Source=str(source), Name=parts[-1])
        # User-profile components use HKCU key paths, as required by MSI ICE38.
        add(
            component,
            "RegistryValue",
            Root="HKCU",
            Key=r"Software\uBridge\Installer\Files",
            Name=file_id,
            Type="string",
            Value=f"[#{file_id}]",
            KeyPath="yes",
        )
        add(feature, "ComponentRef", Id=component_id)
    anchor = add(
        install,
        "Component",
        Id="uBridgeShortcuts",
        Guid="{536750DE-246A-59B9-8D37-2A1443485A22}",
        Bitness="always64",
    )
    add(
        anchor,
        "RegistryValue",
        Root="HKCU",
        Key=r"Software\uBridge\Installer",
        Name="Version",
        Type="string",
        Value=VERSION,
        KeyPath="yes",
    )
    add(
        anchor,
        "RegistryValue",
        Root="HKCU",
        Key=r"Software\uBridge\Installer",
        Name="Directory",
        Type="string",
        Value="[INSTALLFOLDER]",
    )
    add(
        anchor,
        "RegistryValue",
        Root="HKCU",
        Key=r"Software\uBridge\Installer",
        Name="Session",
        Type="string",
        Value="[ProductCode]|[Date]|[Time]",
    )
    add(
        anchor,
        "Shortcut",
        Id="uBridgeStartMenu",
        Directory="uBridgeMenu",
        Name="uBridge",
        Target="[INSTALLFOLDER]uBridge.exe",
        Arguments="--configure",
        WorkingDirectory="INSTALLFOLDER",
        Icon="uBridgeIcon",
    )
    add(
        anchor,
        "Shortcut",
        Id="uBridgeGuide",
        Directory="uBridgeMenu",
        Name="uBridge guide",
        Target="[INSTALLFOLDER]docs\\BRIDGE.md",
        WorkingDirectory="INSTALLFOLDER",
    )
    for directory_id in [
        "LocalPrograms",
        "INSTALLFOLDER",
        "uBridgeMenu",
        *(element.attrib["Id"] for path, element in directories.items() if path),
    ]:
        add(
            anchor,
            "RemoveFolder",
            Id=identifier("R", directory_id),
            Directory=directory_id,
            On="uninstall",
        )
    add(feature, "ComponentRef", Id="uBridgeShortcuts")
    ET.SubElement(package, f"{{{UI_NAMESPACE}}}WixUI", {"Id": "WixUI_Minimal"})
    add(package, "Property", Id="WIXUI_EXITDIALOGOPTIONALCHECKBOXTEXT", Value="Run uBridge")
    add(package, "Property", Id="WIXUI_EXITDIALOGOPTIONALCHECKBOX", Value="1")
    add(
        package,
        "CustomAction",
        Id="RunuBridge",
        FileRef=identifier("F", "uBridge.exe"),
        ExeCommand="--configure",
        Return="asyncNoWait",
        Execute="immediate",
        Impersonate="yes",
    )
    ui = add(package, "UI")
    add(
        ui,
        "Publish",
        Dialog="ExitDialog",
        Control="Finish",
        Event="DoAction",
        Value="RunuBridge",
        Condition="WIXUI_EXITDIALOGOPTIONALCHECKBOX = 1 AND NOT Installed",
        Order="1",
    )

    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    escaped = (
        text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\par\n")
    )
    license_path = work / "license.rtf"
    license_path.write_text(
        r"{\rtf1\ansi\deff0 {\fonttbl {\f0 Segoe UI;}}\fs20 " + escaped + "}", encoding="ascii"
    )
    add(package, "WixVariable", Id="WixUILicenseRtf", Value=str(license_path))
    path = work / "uBridge.wxs"
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def build():
    command, extension = toolchain()
    work = ROOT / ".cache/msi-build"
    work.mkdir(parents=True, exist_ok=True)
    source = author(payload(), work)
    output = ROOT / "dist" / f"uBridge-{VERSION}-windows-x64.msi"
    output.parent.mkdir(exist_ok=True)
    subprocess.run(
        [
            *command,
            "build",
            "-arch",
            "x64",
            "-ext",
            str(extension),
            "-intermediateFolder",
            str(work / "obj"),
            "-pdbtype",
            "none",
            "-o",
            str(output),
            str(source),
        ],
        check=True,
        cwd=ROOT,
    )
    print(output.name, output.stat().st_size, hashlib.sha256(output.read_bytes()).hexdigest())
    return output


if __name__ == "__main__":
    build()
