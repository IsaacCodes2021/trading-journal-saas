"""The short label shown for a browser: made from fixed words, never the header itself."""

import pytest

from tradejournal.api.device import describe_device

CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
EDGE_WINDOWS = CHROME_WINDOWS + " Edg/124.0.0.0"
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"
SAFARI_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
SAFARI_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
CHROME_IOS = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/124.0.0.0 Mobile/15E148 Safari/604.1"
CHROME_ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
OPERA_WINDOWS = CHROME_WINDOWS + " OPR/110.0.0.0"
CHROMEBOOK = (
    "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@pytest.mark.parametrize(
    ("user_agent", "label"),
    [
        (CHROME_WINDOWS, "Chrome on Windows"),
        (EDGE_WINDOWS, "Edge on Windows"),  # Edge also says Chrome and Safari
        (OPERA_WINDOWS, "Opera on Windows"),
        (FIREFOX_LINUX, "Firefox on Linux"),
        (SAFARI_MAC, "Safari on Mac"),
        (SAFARI_IPHONE, "Safari on iPhone"),  # an iPhone's UA says "like Mac OS X"
        (CHROME_IOS, "Chrome on iPhone"),
        (CHROME_ANDROID, "Chrome on Android"),  # an Android UA says "Linux"
        (CHROMEBOOK, "Chrome on ChromeOS"),
    ],
)
def test_a_known_browser_is_named_with_its_system(user_agent, label):
    assert describe_device(user_agent) == label


@pytest.mark.parametrize("user_agent", [None, "", "curl/8.4.0 something", "x" * 1000])
def test_anything_else_is_just_an_unknown_device(user_agent):
    assert describe_device(user_agent) == "Unknown device"


def test_only_what_is_known_is_said_and_the_header_is_never_echoed():
    assert describe_device("Windows") == "Windows"
    assert describe_device("Firefox/1.0") == "Firefox"
    hostile = "<script>alert(1)</script> Chrome/1 Windows; DROP TABLE sessions"
    label = describe_device(hostile)
    assert label == "Chrome on Windows" and "<" not in label and "DROP" not in label
