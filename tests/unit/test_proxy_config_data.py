from nginx_proxy.ProxyConfigData import ProxyConfigData


def test_print_extra_uses_short_ids_for_injected_backend_map(capsys):
    backend_id = "da3acafcf76f158acaa8eb3643b738b586475d8e141cafe3dabca28f7ec385f6"

    ProxyConfigData.printextra(
        "      ",
        {
            "injected": ["client_max_body_size 0"],
            "injected_by_backend": {backend_id: ["client_max_body_size 0"]},
        },
    )

    output = capsys.readouterr().out
    assert "injected_by_backend : {'da3acafcf76f': ['client_max_body_size 0']}" in output
    assert backend_id not in output
