"""ONVIF camera auto-discovery via WS-Discovery and SOAP device queries.

Uses raw UDP multicast for WS-Discovery and httpx for ONVIF SOAP calls.
No heavy ONVIF library dependencies.
"""

import asyncio
import hashlib
import logging
import os
import socket
import uuid
import xml.etree.ElementTree as ET
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_discovery_pool = ThreadPoolExecutor(max_workers=2)

# WS-Discovery multicast address and port
WS_DISCOVERY_ADDR = "239.255.255.250"
WS_DISCOVERY_PORT = 3702

# XML namespace map for parsing SOAP responses
NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "d": "http://schemas.xmlsoap.org/ws/2005/04/discovery",
    "dn": "http://www.onvif.org/ver10/network/wsdl",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "wsa": "http://schemas.xmlsoap.org/ws/2004/08/addressing",
    "wsdd": "http://schemas.xmlsoap.org/ws/2005/04/discovery",
}

# WS-Discovery Probe envelope template.
# Targets ONVIF NetworkVideoTransmitter devices on the local network.
WS_DISCOVERY_PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope
  xmlns:s="http://www.w3.org/2003/05/soap-envelope"
  xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
  xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
  xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <s:Header>
    <a:Action s:mustUnderstand="1">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action>
    <a:MessageID>uuid:{message_id}</a:MessageID>
    <a:ReplyTo>
      <a:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:Address>
    </a:ReplyTo>
    <a:To s:mustUnderstand="1">urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>
  </s:Header>
  <s:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </s:Body>
</s:Envelope>"""

# SOAP envelope for GetDeviceInformation
GET_DEVICE_INFO_ENVELOPE = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope
  xmlns:s="http://www.w3.org/2003/05/soap-envelope"
  xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
  <s:Header/>
  <s:Body>
    <tds:GetDeviceInformation/>
  </s:Body>
</s:Envelope>"""

# SOAP envelope for GetProfiles (media service)
GET_PROFILES_ENVELOPE = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope
  xmlns:s="http://www.w3.org/2003/05/soap-envelope"
  xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
  <s:Header/>
  <s:Body>
    <trt:GetProfiles/>
  </s:Body>
</s:Envelope>"""

# SOAP envelope for GetStreamUri
GET_STREAM_URI_ENVELOPE = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope
  xmlns:s="http://www.w3.org/2003/05/soap-envelope"
  xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
  xmlns:tt="http://www.onvif.org/ver10/schema">
  <s:Header/>
  <s:Body>
    <trt:GetStreamUri>
      <trt:StreamSetup>
        <tt:Stream>RTP-Unicast</tt:Stream>
        <tt:Transport>
          <tt:Protocol>RTSP</tt:Protocol>
        </tt:Transport>
      </trt:StreamSetup>
      <trt:ProfileToken>{profile_token}</trt:ProfileToken>
    </trt:GetStreamUri>
  </s:Body>
</s:Envelope>"""

# SOAP envelope for GetCapabilities to find media service URL
GET_CAPABILITIES_ENVELOPE = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope
  xmlns:s="http://www.w3.org/2003/05/soap-envelope"
  xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
  <s:Header/>
  <s:Body>
    <tds:GetCapabilities>
      <tds:Category>All</tds:Category>
    </tds:GetCapabilities>
  </s:Body>
</s:Envelope>"""


def _send_ws_discovery_probe(timeout: float = 5.0) -> list[str]:
    """Send a WS-Discovery multicast probe and collect XAddrs from responses.

    Runs in a thread because socket operations are blocking.
    Returns a list of ONVIF device service URLs (XAddrs).
    """
    message_id = str(uuid.uuid4())
    probe_xml = WS_DISCOVERY_PROBE.format(message_id=message_id)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(timeout)

    xaddrs_set: set[str] = set()

    try:
        sock.sendto(probe_xml.encode("utf-8"), (WS_DISCOVERY_ADDR, WS_DISCOVERY_PORT))

        while True:
            try:
                data, _ = sock.recvfrom(65535)
                response_text = data.decode("utf-8", errors="replace")

                # Parse XAddrs from the ProbeMatch response
                try:
                    root = ET.fromstring(response_text)
                except ET.ParseError:
                    continue

                # Look for XAddrs elements anywhere in the response
                for elem in root.iter():
                    tag = elem.tag
                    if tag.endswith("}XAddrs") or tag == "XAddrs":
                        if elem.text:
                            for addr in elem.text.strip().split():
                                addr = addr.strip()
                                if addr.startswith("http"):
                                    xaddrs_set.add(addr)
            except socket.timeout:
                break
            except Exception:
                continue
    except Exception as exc:
        logger.warning("WS-Discovery probe failed. %s", exc)
    finally:
        sock.close()

    return list(xaddrs_set)


def _extract_text(element: ET.Element | None) -> str | None:
    """Safely extract text content from an XML element."""
    if element is not None and element.text:
        return element.text.strip()
    return None


def _find_recursive(root: ET.Element, local_name: str) -> ET.Element | None:
    """Find the first element matching a local name, ignoring namespace."""
    for elem in root.iter():
        tag = elem.tag
        if tag.endswith("}" + local_name) or tag == local_name:
            return elem
    return None


def _find_all_recursive(root: ET.Element, local_name: str) -> list[ET.Element]:
    """Find all elements matching a local name, ignoring namespace."""
    results = []
    for elem in root.iter():
        tag = elem.tag
        if tag.endswith("}" + local_name) or tag == local_name:
            results.append(elem)
    return results


async def _soap_request(
    client: httpx.AsyncClient,
    url: str,
    envelope: str,
    timeout: float = 3.0,
) -> ET.Element | None:
    """Send a SOAP request and parse the XML response."""
    headers = {
        "Content-Type": 'application/soap+xml; charset=utf-8',
    }
    try:
        resp = await client.post(url, content=envelope.encode("utf-8"), headers=headers, timeout=timeout)
        if resp.status_code == 200:
            return ET.fromstring(resp.text)
        # 401 means the device requires auth
        if resp.status_code == 401:
            return None
        return None
    except Exception:
        return None


def _is_auth_fault(root: ET.Element | None) -> bool:
    """Check if a SOAP response contains an authentication fault."""
    if root is None:
        return False
    for elem in root.iter():
        tag = elem.tag
        if tag.endswith("}Fault") or tag == "Fault":
            fault_text = ET.tostring(elem, encoding="unicode", method="text").lower()
            if "not authorized" in fault_text or "sender not authorized" in fault_text:
                return True
    return False


async def _probe_device(onvif_url: str, client: httpx.AsyncClient) -> dict[str, Any] | None:
    """Probe a single ONVIF device to gather its information.

    Returns a dict with device details, or None if the device is unreachable.
    """
    parsed = urlparse(onvif_url)
    ip = parsed.hostname or ""
    port = parsed.port or 80

    result: dict[str, Any] = {
        "ip": ip,
        "port": port,
        "name": "Unknown",
        "manufacturer": "Unknown",
        "model": "Unknown",
        "firmware": None,
        "onvif_url": onvif_url,
        "stream_url": None,
        "profiles": [],
        "auth_required": False,
        "resolution": None,
    }

    # Step 1. Get device information
    info_root = await _soap_request(client, onvif_url, GET_DEVICE_INFO_ENVELOPE)
    if info_root is None:
        # Could not reach the device or it requires auth for this call
        result["auth_required"] = True
    elif _is_auth_fault(info_root):
        result["auth_required"] = True
    else:
        manufacturer = _extract_text(_find_recursive(info_root, "Manufacturer"))
        model = _extract_text(_find_recursive(info_root, "Model"))
        firmware = _extract_text(_find_recursive(info_root, "FirmwareVersion"))

        if manufacturer:
            result["manufacturer"] = manufacturer
        if model:
            result["model"] = model
            result["name"] = f"{manufacturer or ''} {model}".strip() or "Unknown"
        if firmware:
            result["firmware"] = firmware

    # Step 2. Get capabilities to find the media service URL
    media_url = onvif_url.replace("/device_service", "/media_service")
    # Try to get actual media URL from capabilities
    caps_root = await _soap_request(client, onvif_url, GET_CAPABILITIES_ENVELOPE)
    if caps_root is not None and not _is_auth_fault(caps_root):
        media_elem = _find_recursive(caps_root, "Media")
        if media_elem is not None:
            xaddr_elem = _find_recursive(media_elem, "XAddr")
            if xaddr_elem is not None and xaddr_elem.text:
                media_url = xaddr_elem.text.strip()
    elif caps_root is None or _is_auth_fault(caps_root):
        result["auth_required"] = True

    # Step 3. Get profiles
    profiles_root = await _soap_request(client, media_url, GET_PROFILES_ENVELOPE)
    if profiles_root is None or _is_auth_fault(profiles_root):
        result["auth_required"] = True
    else:
        profile_elements = _find_all_recursive(profiles_root, "Profiles")
        profile_tokens: list[str] = []
        for prof in profile_elements:
            token = prof.get("token")
            name_elem = _find_recursive(prof, "Name")
            profile_name = _extract_text(name_elem) or token or "Profile"
            result["profiles"].append(profile_name)
            if token:
                profile_tokens.append(token)

            # Try to extract resolution from the video encoder config
            width_elem = _find_recursive(prof, "Width")
            height_elem = _find_recursive(prof, "Height")
            if width_elem is not None and height_elem is not None:
                w = _extract_text(width_elem)
                h = _extract_text(height_elem)
                if w and h and result["resolution"] is None:
                    result["resolution"] = f"{w}x{h}"

        # Step 4. Get stream URI for the first profile
        if profile_tokens:
            stream_envelope = GET_STREAM_URI_ENVELOPE.format(profile_token=profile_tokens[0])
            stream_root = await _soap_request(client, media_url, stream_envelope)
            if stream_root is not None and not _is_auth_fault(stream_root):
                uri_elem = _find_recursive(stream_root, "Uri")
                if uri_elem is not None and uri_elem.text:
                    result["stream_url"] = uri_elem.text.strip()
            elif stream_root is None or _is_auth_fault(stream_root):
                result["auth_required"] = True

    return result


async def discover_onvif_cameras(timeout: float = 5.0) -> list[dict[str, Any]]:
    """Run full ONVIF discovery. Returns a list of discovered device dicts.

    1. Sends WS-Discovery multicast probe to find devices on LAN
    2. For each discovered device, queries device info, profiles, and stream URIs
    """
    loop = asyncio.get_running_loop()

    # Run WS-Discovery in thread pool (blocking socket ops)
    try:
        xaddrs = await asyncio.wait_for(
            loop.run_in_executor(_discovery_pool, _send_ws_discovery_probe, timeout),
            timeout=timeout + 2,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        logger.warning("WS-Discovery multicast failed. %s", exc)
        xaddrs = []

    if not xaddrs:
        return []

    logger.info("WS-Discovery found %d device(s). Probing details.", len(xaddrs))

    # Probe each device concurrently with a per-device timeout
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(verify=False) as client:
        tasks = [_probe_device(url, client) for url in xaddrs]
        done = await asyncio.gather(*tasks, return_exceptions=True)

        seen_ips: set[str] = set()
        for item in done:
            if isinstance(item, Exception):
                logger.debug("Device probe failed. %s", item)
                continue
            if item is None:
                continue
            # Deduplicate by IP (some devices respond on multiple addresses)
            device_ip = item.get("ip", "")
            if device_ip in seen_ips:
                continue
            seen_ips.add(device_ip)
            results.append(item)

    return results


# ---------------------------------------------------------------------------
# WS-Security UsernameToken header generation
# ---------------------------------------------------------------------------

def _ws_security_header(username: str, password: str) -> str:
    """Build a WS-Security UsernameToken SOAP header block.

    Uses the Password Digest method per the ONVIF specification.
    digest = Base64(SHA1(nonce + created + password))
    """
    nonce_bytes = os.urandom(16)
    nonce_b64 = b64encode(nonce_bytes).decode()
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest_input = nonce_bytes + created.encode("utf-8") + password.encode("utf-8")
    digest = b64encode(hashlib.sha1(digest_input).digest()).decode()

    return f"""<s:Header>
    <Security xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
      <UsernameToken>
        <Username>{username}</Username>
        <Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</Password>
        <Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</Nonce>
        <Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</Created>
      </UsernameToken>
    </Security>
  </s:Header>"""


def _ptz_service_url(ip: str, port: int) -> str:
    """Build the ONVIF PTZ service URL for a given camera."""
    return f"http://{ip}:{port}/onvif/ptz_service"


# ---------------------------------------------------------------------------
# PTZ SOAP envelope templates
# ---------------------------------------------------------------------------

_PTZ_CONTINUOUS_MOVE_ENVELOPE = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope
  xmlns:s="http://www.w3.org/2003/05/soap-envelope"
  xmlns:ptz="http://www.onvif.org/ver20/ptz/wsdl"
  xmlns:tt="http://www.onvif.org/ver10/schema">
  {header}
  <s:Body>
    <ptz:ContinuousMove>
      <ptz:ProfileToken>{profile_token}</ptz:ProfileToken>
      <ptz:Velocity>
        <tt:PanTilt x="{pan_speed}" y="{tilt_speed}"/>
        <tt:Zoom x="{zoom_speed}"/>
      </ptz:Velocity>
    </ptz:ContinuousMove>
  </s:Body>
</s:Envelope>"""

_PTZ_STOP_ENVELOPE = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope
  xmlns:s="http://www.w3.org/2003/05/soap-envelope"
  xmlns:ptz="http://www.onvif.org/ver20/ptz/wsdl">
  {header}
  <s:Body>
    <ptz:Stop>
      <ptz:ProfileToken>{profile_token}</ptz:ProfileToken>
      <ptz:PanTilt>true</ptz:PanTilt>
      <ptz:Zoom>true</ptz:Zoom>
    </ptz:Stop>
  </s:Body>
</s:Envelope>"""

_PTZ_GET_PRESETS_ENVELOPE = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope
  xmlns:s="http://www.w3.org/2003/05/soap-envelope"
  xmlns:ptz="http://www.onvif.org/ver20/ptz/wsdl">
  {header}
  <s:Body>
    <ptz:GetPresets>
      <ptz:ProfileToken>{profile_token}</ptz:ProfileToken>
    </ptz:GetPresets>
  </s:Body>
</s:Envelope>"""

_PTZ_GOTO_PRESET_ENVELOPE = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope
  xmlns:s="http://www.w3.org/2003/05/soap-envelope"
  xmlns:ptz="http://www.onvif.org/ver20/ptz/wsdl">
  {header}
  <s:Body>
    <ptz:GotoPreset>
      <ptz:ProfileToken>{profile_token}</ptz:ProfileToken>
      <ptz:PresetToken>{preset_token}</ptz:PresetToken>
    </ptz:GotoPreset>
  </s:Body>
</s:Envelope>"""


# ---------------------------------------------------------------------------
# PTZ internal helpers
# ---------------------------------------------------------------------------

async def _ptz_command(
    ip: str,
    port: int,
    username: str | None,
    password: str | None,
    envelope_template: str,
    **fmt_kwargs: object,
) -> ET.Element | None:
    """Send a PTZ SOAP command and return the parsed XML root (or None on failure).

    Handles WS-Security header injection and shared httpx client lifecycle.
    """
    header = _ws_security_header(username, password) if username and password else "<s:Header/>"
    envelope = envelope_template.format(header=header, **fmt_kwargs)
    url = _ptz_service_url(ip, port)
    async with httpx.AsyncClient(verify=False) as client:
        return await _soap_request(client, url, envelope, timeout=5.0)


# ---------------------------------------------------------------------------
# PTZ public methods
# ---------------------------------------------------------------------------

async def ptz_continuous_move(
    ip: str,
    port: int,
    username: str | None,
    password: str | None,
    profile_token: str,
    pan_speed: float,
    tilt_speed: float,
    zoom_speed: float,
) -> bool:
    """Send a ContinuousMove SOAP request to start PTZ movement.

    Returns True if the camera accepted the command.
    """
    root = await _ptz_command(
        ip, port, username, password,
        _PTZ_CONTINUOUS_MOVE_ENVELOPE,
        profile_token=profile_token,
        pan_speed=pan_speed,
        tilt_speed=tilt_speed,
        zoom_speed=zoom_speed,
    )
    return root is not None and not _is_auth_fault(root)


async def ptz_stop(
    ip: str,
    port: int,
    username: str | None,
    password: str | None,
    profile_token: str,
) -> bool:
    """Send a Stop SOAP request to halt all PTZ movement.

    Returns True if the camera accepted the command.
    """
    root = await _ptz_command(
        ip, port, username, password,
        _PTZ_STOP_ENVELOPE,
        profile_token=profile_token,
    )
    return root is not None and not _is_auth_fault(root)


async def ptz_get_presets(
    ip: str,
    port: int,
    username: str | None,
    password: str | None,
    profile_token: str,
) -> list[dict[str, str]]:
    """Fetch saved PTZ presets from the camera.

    Returns a list of dicts with "token" and "name" keys.
    """
    root = await _ptz_command(
        ip, port, username, password,
        _PTZ_GET_PRESETS_ENVELOPE,
        profile_token=profile_token,
    )
    if root is None or _is_auth_fault(root):
        return []

    presets: list[dict[str, str]] = []
    for elem in _find_all_recursive(root, "Preset"):
        token = elem.get("token", "")
        name_elem = _find_recursive(elem, "Name")
        name = _extract_text(name_elem) or token
        if token:
            presets.append({"token": token, "name": name})
    return presets


async def ptz_goto_preset(
    ip: str,
    port: int,
    username: str | None,
    password: str | None,
    profile_token: str,
    preset_token: str,
) -> bool:
    """Move the camera to a saved preset position.

    Returns True if the camera accepted the command.
    """
    root = await _ptz_command(
        ip, port, username, password,
        _PTZ_GOTO_PRESET_ENVELOPE,
        profile_token=profile_token,
        preset_token=preset_token,
    )
    return root is not None and not _is_auth_fault(root)
