# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the Ranger API client."""

from unittest import TestCase, mock

from apache_ranger.exceptions import RangerServiceException
from apache_ranger.model.ranger_service import RangerService

from ranger_client import RangerAPIClient, RangerAPIError
from utils import content_hash


class TestRangerAPIClient(TestCase):
    """Tests for Ranger service API methods."""

    def setUp(self):
        """Set up an API client with a mocked Ranger client."""
        self.ranger_client = mock.patch("ranger_client.RangerClient").start()
        self.addCleanup(mock.patch.stopall)
        self.client = RangerAPIClient("http://ranger", ("admin", "password"))
        self.ranger_client_instance = self.ranger_client.return_value
        self.service = RangerService({"name": "example-service"})

    def test_list_services(self):
        """Services returned by Ranger are passed through."""
        services = [self.service]
        self.ranger_client_instance.find_services.return_value = services

        self.assertEqual(self.client.list_services(), services)
        self.ranger_client_instance.find_services.assert_called_once_with({})

    def test_list_services_returns_empty_list_when_ranger_returns_none(self):
        """An absent Ranger response is represented by an empty list."""
        self.ranger_client_instance.find_services.return_value = None

        self.assertEqual(self.client.list_services(), [])

    def test_list_services_wraps_ranger_errors(self):
        """Ranger service errors have the client-specific exception type."""
        self.ranger_client_instance.find_services.side_effect = RangerServiceException(
            mock.MagicMock(), mock.Mock(content=b"", status_code=500)
        )

        with self.assertRaisesRegex(RangerAPIError, "Failed to list services"):
            self.client.list_services()

    def test_get_service_by_name(self):
        """The named service returned by Ranger is passed through."""
        self.ranger_client_instance.get_service.return_value = self.service

        self.assertIs(self.client.get_service_by_name(self.service.name), self.service)
        self.ranger_client_instance.get_service.assert_called_once_with(self.service.name)

    def test_get_service_by_name_returns_none_when_absent(self):
        """A missing service is reported as an expected absent result."""
        self.ranger_client_instance.get_service.return_value = None

        self.assertIsNone(self.client.get_service_by_name("missing-service"))

    def test_create_service(self):
        """The created service returned by Ranger is passed through."""
        self.ranger_client_instance.create_service.return_value = self.service

        self.assertIs(self.client.create_service(self.service), self.service)
        self.ranger_client_instance.create_service.assert_called_once_with(self.service)

    def test_create_service_raises_when_ranger_returns_none(self):
        """A missing creation response is reported as an API error."""
        self.ranger_client_instance.create_service.return_value = None

        with self.assertRaisesRegex(RangerAPIError, "no response from server"):
            self.client.create_service(self.service)

    def test_create_service_wraps_ranger_errors(self):
        """Ranger service errors have the client-specific exception type."""
        self.ranger_client_instance.create_service.side_effect = RangerServiceException(
            mock.MagicMock(), mock.Mock(content=b"", status_code=500)
        )

        with self.assertRaisesRegex(RangerAPIError, "Failed to create service"):
            self.client.create_service(self.service)

    def test_delete_service_by_id(self):
        """The service ID is passed to Ranger for deletion."""
        self.client.delete_service_by_id(42)

        self.ranger_client_instance.delete_service_by_id.assert_called_once_with(42)

    def test_delete_service_by_id_wraps_ranger_errors(self):
        """Ranger service errors have the client-specific exception type."""
        self.ranger_client_instance.delete_service_by_id.side_effect = RangerServiceException(
            mock.MagicMock(), mock.Mock(content=b"", status_code=500)
        )

        with self.assertRaisesRegex(RangerAPIError, "Failed to delete service"):
            self.client.delete_service_by_id(42)


class TestContentHash(TestCase):
    """Tests for content hashing."""

    def test_content_hash(self):
        """Content hashes are stable SHA-256 hexadecimal digests."""
        self.assertEqual(
            content_hash("ranger"),
            "dbc4a04327176e6577b4da46df04564150053960eba5d89587dad1f76a818d80",
        )
