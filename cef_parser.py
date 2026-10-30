"""UniFi CEF decoding; preserve original packets and ignore malformed headers."""
import re


def unescape(value, header=False):
    allowed = {'\\': '\\', '|': '|'} if header else {'\\': '\\', '=': '=', 'n': '\n', 'r': '\r', '|': '|'}
    return re.sub(r'\\(.)', lambda match: allowed.get(match[1], match[0]), value)


def split_header(value):
    fields = []
    start = 0
    index = 0
    while index < len(value) and len(fields) < 7:
        if value[index] == '\\':
            index += 2
            continue
        if value[index] == '|':
            fields.append(unescape(value[start:index], header=True))
            start = index + 1
        index += 1
    if len(fields) != 7:
        return None
    return fields + [value[start:]]


def cef_level(severity, fallback):
    named = {'unknown': 'INFO', 'low': 'INFO', 'medium': 'WARN', 'high': 'ERROR', 'very-high': 'CRITICAL'}
    normalized = severity.strip().lower()
    if normalized in named:
        return named[normalized]
    if normalized.isdigit() and 0 <= int(normalized) <= 10:
        number = int(normalized)
        return 'CRITICAL' if number >= 9 else 'ERROR' if number >= 7 else 'WARN' if number >= 4 else 'INFO'
    return fallback


def enrich_unifi(entry):
    candidate = entry['message'].lstrip('\ufeff')
    if candidate.startswith('- '):
        candidate = candidate[2:].lstrip('\ufeff')
    # Some exporters omit a syslog tag and place CEF directly after the hostname.
    envelope = re.match(r'^(?:<\d{1,3}>)?[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2}\s+(\S+)\s+(CEF:\d+\|.*)$', entry['raw'], re.S)
    if envelope:
        candidate = envelope[2]
    direct = re.match(r'^(?:<\d{1,3}>)?(CEF:[01]\|.*)$', entry['raw'], re.S)
    if direct:
        candidate = direct[1]
    if not candidate.startswith(('CEF:0|', 'CEF:1|')):
        return entry
    parts = split_header(candidate)
    if not parts:
        return entry
    version, vendor, product, device_version, event_id, event_name, severity, extension = parts
    if vendor.casefold() != 'ubiquiti' or not product.casefold().startswith('unifi'):
        return entry
    markers = list(re.finditer(r'(?<!\S)([A-Za-z][A-Za-z0-9]*)=', extension))
    fields = {marker[1]: unescape(extension[marker.end():markers[index+1].start() if index+1 < len(markers) else len(extension)].rstrip())
              for index, marker in enumerate(markers)}
    def first(*keys):
        return next((fields[key] for key in keys if fields.get(key) and fields[key].strip().lower() not in ('-', '<na>', 'unknown')), '')
    host = first('UNIFIdeviceName', 'dvchost', 'UNIFIhost') or (envelope[1] if envelope else entry['host'])
    category = first('UNIFIcategory')
    subtype = first('UNIFIsubCategory')
    message = first('msg')
    entry.update(
        host=host,
        app=' / '.join(part for part in ('UniFi', category, subtype) if part),
        level=cef_level(severity, entry['level']),
        message=f'{event_name} [{event_id}]' + (f': {message}' if message else ''),
        metadata={'format': 'CEF', 'vendor': vendor, 'product': product, 'device_version': device_version,
                  'event_id': event_id, 'event_name': event_name, 'cef_severity': severity,
                  'category': category, 'sub_category': subtype, 'reporting_host': first('UNIFIhost'),
                  'extensions': fields},
    )
    return entry
