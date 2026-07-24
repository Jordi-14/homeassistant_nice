"""INFO response parsing for NHK devices."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ...errors import NiceProtocolError
from ...models.device import NiceDeviceInfo, NiceServiceCapability


def _element_label(element: ET.Element) -> str:
    element_id = element.get("id")
    return element.tag if element_id is None else f'{element.tag}[@id="{element_id}"]'


def _split_values(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _parse_capabilities(
    root: ET.Element,
    container_tag: str,
) -> tuple[NiceServiceCapability, ...]:
    capabilities: list[NiceServiceCapability] = []

    def walk(node: ET.Element, path: str) -> None:
        for child in list(node):
            if child.tag == container_tag:
                for capability in list(child):
                    values_raw = capability.get("values")
                    capabilities.append(
                        NiceServiceCapability(
                            owner=node.tag,
                            owner_id=node.get("id"),
                            name=capability.tag,
                            path=f"{path}/{container_tag}/{capability.tag}",
                            value_type=capability.get("type"),
                            permission=capability.get("perm"),
                            values_raw=values_raw,
                            values=_split_values(values_raw),
                        )
                    )
            walk(child, f"{path}/{_element_label(child)}")

    walk(root, _element_label(root))
    return tuple(capabilities)


def parse_info_xml(info_xml: str, device_id: int = 1) -> NiceDeviceInfo:
    """Parse INFO XML into static metadata and advertised capabilities."""
    try:
        root = ET.fromstring(info_xml)
    except ET.ParseError as err:
        raise NiceProtocolError(f"Invalid INFO XML: {err}") from err

    interface = root.find("Interface")
    device = root.find(f"./Devices/Device[@id='{device_id}']")
    if device is None:
        device = root.find("./Devices/Device")

    def find_text(node: ET.Element | None, name: str) -> str | None:
        if node is None:
            return None
        value = node.findtext(name)
        return value.strip() if value and value.strip() else None

    return NiceDeviceInfo(
        interface_hw_version=find_text(interface, "VersionHW"),
        interface_fw_version=find_text(interface, "VersionFW"),
        interface_manufacturer=find_text(interface, "Manuf"),
        interface_product=find_text(interface, "Prod"),
        interface_serial=find_text(interface, "SerialNr"),
        device_type=find_text(device, "Type"),
        device_manufacturer=find_text(device, "Manuf"),
        device_product=find_text(device, "Prod"),
        device_description=find_text(device, "Desc"),
        device_hw_version=find_text(device, "VersionHW"),
        device_fw_version=find_text(device, "VersionFW"),
        device_serial=find_text(device, "SerialNr"),
        device_product_detail=find_text(device, "ProdDTL"),
        protocol_version=root.get("protocolVersion"),
        services=_parse_capabilities(root, "Services"),
        properties=_parse_capabilities(root, "Properties"),
    )


def device_info_supports_nhk_status(info: NiceDeviceInfo, device_id: int = 1) -> bool:
    """Return true when INFO advertises readable NHK DoorStatus."""
    target_device_id = str(device_id)
    for prop in info.properties:
        if prop.name != "DoorStatus":
            continue
        if prop.owner != "Device" or prop.owner_id not in {None, target_device_id}:
            continue
        if prop.readable:
            return True
    return False
