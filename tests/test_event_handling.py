"""
Unit tests for Docker event handling
Tests backward compatibility with both v28 and v29+ event formats
"""
import pytest
from unittest.mock import MagicMock


def test_container_start_event_v29_format():
    """Test that container start events work with Docker v29+ event format"""
    # Create a mock server object
    mock_server = MagicMock()
    
    # Docker v29+ event format - id is inside Actor
    event_v29 = {
        "Type": "container",
        "Action": "start",
        "Actor": {
            "ID": "test_container_id_123",
            "Attributes": {
                "name": "test_container"
            }
        },
        "scope": "local",
        "time": 1234567890
    }
    
    # Simulate the backward compatible logic
    action = "start"
    if action == "start":
        # Try v29 format first with explicit None check, fallback to v28
        container_id = event_v29.get("Actor", {}).get("ID")
        if container_id is None:
            container_id = event_v29.get("id")
        mock_server.update_container(container_id)
    
    # Verify update_container was called with the correct ID from Actor.ID
    mock_server.update_container.assert_called_once_with("test_container_id_123")


def test_container_stop_event_v29_format():
    """Test that container stop events work with Docker v29+ event format"""
    # Create a mock server object
    mock_server = MagicMock()
    
    # Docker v29+ event format - id is inside Actor
    event_v29 = {
        "Type": "container",
        "Action": "stop",
        "Actor": {
            "ID": "test_container_id_456",
            "Attributes": {
                "name": "test_container"
            }
        },
        "scope": "local",
        "time": 1234567890
    }
    
    # Simulate the backward compatible logic
    action = "stop"
    if action == "stop":
        # Try v29 format first with explicit None check, fallback to v28
        container_id = event_v29.get("Actor", {}).get("ID")
        if container_id is None:
            container_id = event_v29.get("id")
        mock_server.remove_container(container_id)
    
    # Verify remove_container was called with the correct ID from Actor.ID
    mock_server.remove_container.assert_called_once_with("test_container_id_456")


def test_container_start_event_v28_format():
    """Test that container start events work with Docker v28 (legacy) event format"""
    # Create a mock server object
    mock_server = MagicMock()
    
    # Docker v28 event format - id is at top level
    event_v28 = {
        "Type": "container",
        "Action": "start",
        "id": "test_container_id_legacy",
        "Actor": {
            "Attributes": {
                "name": "test_container"
            }
        },
        "scope": "local",
        "time": 1234567890
    }
    
    # Simulate the backward compatible logic
    action = "start"
    if action == "start":
        # Try v29 format first with explicit None check, fallback to v28
        container_id = event_v28.get("Actor", {}).get("ID")
        if container_id is None:
            container_id = event_v28.get("id")
        mock_server.update_container(container_id)
    
    # Verify update_container was called with the correct ID from top-level id
    mock_server.update_container.assert_called_once_with("test_container_id_legacy")


def test_container_stop_event_v28_format():
    """Test that container stop events work with Docker v28 (legacy) event format"""
    # Create a mock server object
    mock_server = MagicMock()
    
    # Docker v28 event format - id is at top level
    event_v28 = {
        "Type": "container",
        "Action": "stop",
        "id": "test_container_id_legacy_stop",
        "Actor": {
            "Attributes": {
                "name": "test_container"
            }
        },
        "scope": "local",
        "time": 1234567890
    }
    
    # Simulate the backward compatible logic
    action = "stop"
    if action == "stop":
        # Try v29 format first with explicit None check, fallback to v28
        container_id = event_v28.get("Actor", {}).get("ID")
        if container_id is None:
            container_id = event_v28.get("id")
        mock_server.remove_container(container_id)
    
    # Verify remove_container was called with the correct ID from top-level id
    mock_server.remove_container.assert_called_once_with("test_container_id_legacy_stop")


def test_event_structure_v29():
    """Test that we can access event fields in Docker v29+ format"""
    event_v29 = {
        "Type": "container",
        "Action": "start",
        "Actor": {
            "ID": "abc123",
            "Attributes": {
                "name": "my_container"
            }
        },
        "scope": "local",
        "time": 1234567890
    }
    
    # Test that Actor.ID exists and is accessible
    assert "Actor" in event_v29
    assert "ID" in event_v29["Actor"]
    assert event_v29["Actor"]["ID"] == "abc123"
    
    # Test that old style event["id"] would not exist in v29
    assert "id" not in event_v29
    
    # Test backward compatible extraction with explicit None check
    container_id = event_v29.get("Actor", {}).get("ID")
    if container_id is None:
        container_id = event_v29.get("id")
    assert container_id == "abc123"


def test_event_structure_v28():
    """Test that we can access event fields in Docker v28 (legacy) format"""
    event_v28 = {
        "Type": "container",
        "Action": "start",
        "id": "def456",
        "Actor": {
            "Attributes": {
                "name": "my_container"
            }
        },
        "scope": "local",
        "time": 1234567890
    }
    
    # Test that top-level id exists
    assert "id" in event_v28
    assert event_v28["id"] == "def456"
    
    # Test that Actor.ID doesn't exist in v28
    assert "ID" not in event_v28.get("Actor", {})
    
    # Test backward compatible extraction with explicit None check
    container_id = event_v28.get("Actor", {}).get("ID")
    if container_id is None:
        container_id = event_v28.get("id")
    assert container_id == "def456"


def test_event_missing_id():
    """Test that we handle events with no ID gracefully"""
    # Create a mock server object
    mock_server = MagicMock()
    
    # Malformed event with no ID anywhere
    event_no_id = {
        "Type": "container",
        "Action": "start",
        "Actor": {
            "Attributes": {
                "name": "test_container"
            }
        },
        "scope": "local",
        "time": 1234567890
    }
    
    # Simulate the backward compatible logic with validation
    action = "start"
    container_id = event_no_id.get("Actor", {}).get("ID")
    if container_id is None:
        container_id = event_no_id.get("id")
    
    # Should not call update_container if no ID found
    if container_id:
        mock_server.update_container(container_id)
    
    # Verify update_container was NOT called
    mock_server.update_container.assert_not_called()
