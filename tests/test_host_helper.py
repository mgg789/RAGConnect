from __future__ import annotations

import unittest

from server_gateway.host_helper import HostHelper


class HostHelperTests(unittest.TestCase):
    def test_parse_compose_services_accepts_json_array(self) -> None:
        payload = b'[{"Service":"lightrag"},{"Service":"server-gateway"}]'
        parsed = HostHelper.parse_compose_services(payload)
        self.assertEqual([item["Service"] for item in parsed], ["lightrag", "server-gateway"])

    def test_parse_compose_services_accepts_newline_delimited_json(self) -> None:
        payload = b'{"Service":"lightrag","State":"running"}\n{"Service":"server-gateway","State":"running"}\n'
        parsed = HostHelper.parse_compose_services(payload)
        self.assertEqual([item["Service"] for item in parsed], ["lightrag", "server-gateway"])


if __name__ == "__main__":
    unittest.main()
