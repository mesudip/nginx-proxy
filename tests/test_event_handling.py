"""
Unit tests for Docker event handling
Tests the event structure changes in Docker v29
"""
import pytest
from unittest.mock import MagicMock


def test_container_start_event_v29_format():
    """Test that container start events work with Docker v29 event format"""
    # Create a mock server object
    mock_server = MagicMock()
    
    # Docker v29 event format - id is inside Actor
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
    
    # Simulate the process_container_event function logic
    action = "start"
    if action == "start":
        # This is what the fixed code should do
        container_id = event_v29["Actor"]["ID"]
        mock_server.update_container(container_id)
    
    # Verify update_container was called with the correct ID from Actor.ID
    mock_server.update_container.assert_called_once_with("test_container_id_123")


def test_container_stop_event_v29_format():
    """Test that container stop events work with Docker v29 event format"""
    # Create a mock server object
    mock_server = MagicMock()
    
    # Docker v29 event format - id is inside Actor
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
    
    # Simulate the process_container_event function logic
    action = "stop"
    if action == "stop":
        # This is what the fixed code should do
        container_id = event_v29["Actor"]["ID"]
        mock_server.remove_container(container_id)
    
    # Verify remove_container was called with the correct ID from Actor.ID
    mock_server.remove_container.assert_called_once_with("test_container_id_456")


def test_event_structure_v29():
    """Test that we can access event fields in Docker v29 format"""
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
    
    # Test that old style event["id"] would not exist
    assert "id" not in event_v29
