from nginx_proxy.Location import Location


def test_spaced_equals_assignment_participates_in_scalar_comparison():
    location = Location("/")

    location.update_extras(
        {
            "injected_by_backend": {
                "small": ["client_max_body_size = 5m"],
                "large": ["client_max_body_size 2g"],
            }
        }
    )

    assert location.extras["injected"] == ["client_max_body_size 2g"]


def test_equals_inside_whitespace_syntax_value_is_preserved():
    location = Location("/")

    location.update_extras({"injected_by_backend": {"backend": ["proxy_set_header Cookie=session=a=b"]}})

    assert location.extras["injected"] == ["proxy_set_header Cookie=session=a=b"]


def test_conflict_warning_uses_short_backend_ids(capsys):
    location = Location("/")
    first_id = "a" * 64
    second_id = "b" * 64

    location.update_extras(
        {
            "injected_by_backend": {
                first_id: ["client_max_body_size 5m"],
                second_id: ["client_max_body_size 2g"],
            }
        }
    )

    output = capsys.readouterr().out
    assert f"existing_backend={first_id[:12]}" in output
    assert f"incoming_backend={second_id[:12]}" in output
    assert first_id not in output
    assert second_id not in output
