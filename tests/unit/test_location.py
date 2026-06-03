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
