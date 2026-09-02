from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from nginx_proxy.Host import Host
from nginx_proxy.post_processors.ssl_certificate_processor import SslCertificateProcessor


def _cert(expiry: datetime):
    return SimpleNamespace(not_valid_after_utc=expiry)


def _build_processor(cert_expiries: dict, threshold_days=30):
    """cert_expiries maps certificate file name -> expiry datetime (None means the file is missing)."""
    key_store = Mock()

    def by_cert_id(name):
        expiry = cert_expiries.get(name)
        return None if expiry is None else (Mock(), [_cert(expiry)])

    def by_domain(domain):
        expiry = cert_expiries.get(domain)
        return None if expiry is None else (domain, Mock(), [_cert(expiry)])

    key_store.find_key_and_cert_by_cert_id.side_effect = by_cert_id
    key_store.find_key_and_cert_by_domain.side_effect = by_domain
    del key_store.find_key_and_cert_covering_domain

    backend_info = SimpleNamespace(
        backend=Mock(),
        key_store=key_store,
        certapi_url="https://certapi.example.com",
        use_certapi_server=True,
        batch_domains=True,
        cert_manager=None,
        certapi_client=Mock(),
        challenge_store=None,
    )
    server = SimpleNamespace(config={"certapi": {"url": "https://certapi.example.com"}}, enqueue_reload=Mock())
    nginx = SimpleNamespace(challenge_dir="./.run_data/acme-challenges/")

    with (
        patch(
            "nginx_proxy.post_processors.ssl_certificate_processor.build_certificate_backend",
            return_value=backend_info,
        ),
        patch("nginx_proxy.post_processors.ssl_certificate_processor.RenewalManager") as renewal_cls,
    ):
        renewal = Mock()
        renewal.update_threshold_secs = threshold_days * 24 * 3600
        renewal.sleep_slack_seconds = 300
        renewal_cls.return_value = renewal
        processor = SslCertificateProcessor(
            nginx, server=server, update_threshold_days=threshold_days, ssl_dir="./.run_data"
        )
    return processor, renewal


def test_status_table_lists_each_domain_with_remaining_time_and_state(capsys):
    now = datetime.now(timezone.utc)
    processor, _ = _build_processor(
        {
            "*.example.com": now + timedelta(days=80, hours=3, minutes=4, seconds=5),
            "api.example.com": now + timedelta(days=80),
            "old.example.net": now - timedelta(days=2),
            "soon.example.net": now + timedelta(days=5),
        }
    )
    hosts = [
        Host("api.example.com", 443, {"https"}),
        Host("old.example.net", 443, {"https"}),
        Host("soon.example.net", 443, {"https"}),
        Host("new.example.net", 443, {"https"}),
    ]

    processor.process_ssl_certificates(hosts)
    lines = capsys.readouterr().out.splitlines()

    assert "[SSL Refresh Thread] SSL certificate status:" in lines
    rows = {line.split(" - ", 1)[0].strip(): line.split(" - ", 1)[1] for line in lines if line.startswith("  ")}
    # every hostname is padded to the same column
    assert len({line.index(" - ") for line in lines if line.startswith("  ")}) == 1
    # no microseconds, zero padded time
    assert rows["api.example.com"].startswith("80 days, 03:04:0") and rows["api.example.com"].endswith(
        "(*.example.com)"
    )
    assert rows["old.example.net"].startswith("EXPIRED 2 days, 00:00:0") and rows["old.example.net"].endswith(" ago")
    assert rows["soon.example.net"].startswith("4 days, 23:59:5")
    assert "new.example.net" not in rows
    assert lines[-1] == "[SSL Refresh Thread] Selfsigned: new.example.net"
    # Earliest real certificate is soon.example.net, already inside the threshold -> refresh needed.
    assert any(
        line.startswith(
            "[SSL Refresh Thread] Looks like we need to refresh certificates that are about to expire (soon.example.net"
        )
        for line in lines
    )


def test_next_check_is_expiry_minus_threshold_plus_slack(capsys):
    now = datetime.now(timezone.utc)
    processor, _ = _build_processor({"api.example.com": now + timedelta(days=40)})

    processor.process_ssl_certificates([Host("api.example.com", 443, {"https"})])
    out = capsys.readouterr().out

    # 40d - 30d threshold + 5m slack -> 10 days
    assert "[SSL Refresh Thread] All the certificates are up to date sleeping for 10 days." in out


def test_sleep_is_capped_at_renewal_manager_max_sleep(capsys):
    now = datetime.now(timezone.utc)
    processor, renewal = _build_processor({"api.example.com": now + timedelta(days=89)})
    renewal.max_sleep_seconds = 32 * 24 * 3600

    processor.process_ssl_certificates([Host("api.example.com", 443, {"https"})])

    assert "sleeping for 32 days." in capsys.readouterr().out


def test_status_is_only_logged_when_something_changed(capsys):
    now = datetime.now(timezone.utc)
    processor, _ = _build_processor({"api.example.com": now + timedelta(days=40)})
    hosts = [Host("api.example.com", 443, {"https"})]

    processor.process_ssl_certificates(hosts)
    first = capsys.readouterr().out
    assert "[SSL Refresh Thread] SSL certificate status:" in first

    processor.process_ssl_certificates(hosts)
    assert "SSL certificate status" not in capsys.readouterr().out

    processor.process_ssl_certificates(hosts + [Host("www.example.com", 443, {"https"})])
    third = capsys.readouterr().out
    assert "SSL certificate status" in third
    assert "[SSL Refresh Thread] Selfsigned: www.example.com" in third

    processor.log_certificate_status(hosts, force=True)
    assert "SSL certificate status" in capsys.readouterr().out


def test_dry_run_does_not_log_status(capsys):
    now = datetime.now(timezone.utc)
    processor, _ = _build_processor({"api.example.com": now + timedelta(days=40)})

    processor.process_ssl_certificates([Host("api.example.com", 443, {"https"})], update_watch_domains=False)

    assert "SSL certificate status" not in capsys.readouterr().out


def test_start_announces_thread_and_next_check(capsys):
    now = datetime.now(timezone.utc)
    processor, renewal = _build_processor({"api.example.com": now + timedelta(days=40)})
    processor.process_ssl_certificates([Host("api.example.com", 443, {"https"})])
    capsys.readouterr()

    processor.start()
    out = capsys.readouterr().out

    renewal.start.assert_called_once_with()
    assert (
        "[SSL Refresh Thread] Started with backend=certapi https://certapi.example.com, "
        "renew threshold 30 days, watching 1 domains"
    ) in out
    assert "[SSL Refresh Thread] All the certificates are up to date sleeping for 10 days." in out


def test_start_without_certificates_reports_nothing_to_watch(capsys):
    processor, _ = _build_processor({})

    processor.start()

    assert "[SSL Refresh Thread] Looks like there are no ssl certificates, sleeping until there's one" in (
        capsys.readouterr().out
    )


def test_renewal_callback_queues_only_one_reload_until_it_has_run(capsys):
    now = datetime.now(timezone.utc)
    processor, _ = _build_processor({"api.example.com": now + timedelta(days=40)})
    hosts = [Host("api.example.com", 443, {"https"})]

    # The renewal worker ticks once a second while a renewal pass is in flight.
    for _ in range(5):
        processor.ssl_renewal_callback()

    processor.server.enqueue_reload.assert_called_once_with(force=True)
    assert capsys.readouterr().out.count("Renewal due, requesting forced nginx reload") == 1

    # The queued reload runs and refreshes the watch set, after which a new renewal may queue again.
    processor.process_ssl_certificates(hosts)
    processor.ssl_renewal_callback()
    assert processor.server.enqueue_reload.call_count == 2


def test_renewal_callback_pending_flag_is_released_when_enqueue_fails():
    now = datetime.now(timezone.utc)
    processor, _ = _build_processor({"api.example.com": now + timedelta(days=40)})
    processor.server.enqueue_reload.side_effect = [RuntimeError("queue closed"), True]

    try:
        processor.ssl_renewal_callback()
    except RuntimeError:
        pass
    processor.ssl_renewal_callback()

    assert processor.server.enqueue_reload.call_count == 2


def test_certificate_change_is_flagged_once_when_a_used_file_gets_a_new_expiry(capsys):
    now = datetime.now(timezone.utc)
    expiries = {"api.example.com": now + timedelta(days=20), "*.example.org": now + timedelta(days=50)}
    processor, _ = _build_processor(expiries)
    hosts = [Host("api.example.com", 443, {"https"}), Host("www.example.org", 443, {"https"})]

    processor.process_ssl_certificates(hosts)
    assert processor.pop_certificate_changes() is False  # first sight of the files is not a change

    processor.process_ssl_certificates(hosts)
    assert processor.pop_certificate_changes() is False  # nothing renewed

    expiries["api.example.com"] = now + timedelta(days=89)  # renewal replaced the file on disk
    processor.process_ssl_certificates(hosts)
    out = capsys.readouterr().out
    assert "Certificates renewed on disk, nginx reload required: api.example.com" in out
    assert processor.pop_certificate_changes() is True
    assert processor.pop_certificate_changes() is False  # consumed


def test_new_or_dry_run_files_do_not_flag_a_certificate_change():
    now = datetime.now(timezone.utc)
    expiries = {"api.example.com": now + timedelta(days=60)}
    processor, _ = _build_processor(expiries)
    processor.process_ssl_certificates([Host("api.example.com", 443, {"https"})])
    processor.pop_certificate_changes()

    # A domain appearing for the first time changes the rendered config anyway, so no force needed.
    expiries["new.example.com"] = now + timedelta(days=60)
    hosts = [Host("api.example.com", 443, {"https"}), Host("new.example.com", 443, {"https"})]
    processor.process_ssl_certificates(hosts)
    assert processor.pop_certificate_changes() is False

    # Dry runs never touch the tracking state.
    expiries["api.example.com"] = now + timedelta(days=89)
    processor.process_ssl_certificates(hosts, update_watch_domains=False)
    assert processor.pop_certificate_changes() is False
