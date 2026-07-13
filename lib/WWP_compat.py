import sys

try:
    from io import open as io_open
except Exception:
    io_open = open


PY3 = sys.version_info[0] >= 3

if PY3:
    text_type = str
    binary_type = bytes
    string_types = (str,)
else:
    text_type = unicode
    binary_type = str
    string_types = (str, unicode)


try:
    import configparser as _configparser
except Exception:
    import ConfigParser as _configparser

configparser = _configparser


try:
    import urllib.parse as urllib_parse
except Exception:
    import urllib as urllib_parse
    import urlparse as _urlparse_compat
    urllib_parse.urlparse = _urlparse_compat.urlparse
    urllib_parse.parse_qs = _urlparse_compat.parse_qs
    urllib_parse.parse_qsl = _urlparse_compat.parse_qsl


try:
    import urllib.request as urllib_request
except Exception:
    import urllib2 as urllib_request


Request = urllib_request.Request
urlopen = urllib_request.urlopen


def read_config_file(parser, file_obj):
    reader = getattr(parser, "read_file", None)
    if callable(reader):
        return reader(file_obj)
    reader = getattr(parser, "readfp", None)
    if callable(reader):
        return reader(file_obj)
    raise AttributeError("Config parser does not expose read_file/readfp")


def decode_to_text(value, encoding="utf-8", errors="strict"):
    if value is None:
        return ""
    if isinstance(value, text_type):
        return value
    try:
        return value.decode(encoding, errors)
    except Exception:
        try:
            return text_type(value)
        except Exception:
            return ""
