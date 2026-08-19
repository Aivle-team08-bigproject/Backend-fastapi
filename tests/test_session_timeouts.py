"""유휴시간(idle timeout)과 절대 타임아웃이 실제로 동작하는지 검증한다.

monkeypatch로 설정값 자체를 짧게 바꿔서 실제로 몇 초 기다려보는 방식이라 이 파일의
테스트들은 다른 테스트보다 느리다 (수 초 단위로 sleep 함).
"""

import time

from tests.test_auth_flow import _login_as_admin


def test_default_absolute_session_lifetime_is_four_hours(settings):
    assert settings.session_normal_ttl_minutes == 240
    assert settings.session_remember_me_ttl_hours == 4


def test_idle_timeout_logs_out_after_inactivity(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "session_idle_timeout_minutes", 1 / 60)  # 1초

    headers = _login_as_admin(client)

    still_ok = client.get("/api/auth/me", headers=headers)
    assert still_ok.status_code == 200

    time.sleep(1.5)

    timed_out = client.get("/api/auth/me", headers=headers)
    assert timed_out.status_code == 401


def test_continued_activity_still_hits_absolute_timeout(client, settings, monkeypatch):
    """유휴시간을 넉넉하게 주고, 계속 업무 API를 호출해서 활동을 유지해도,
    절대 타임아웃(로그인 시각 기준)에는 결국 걸려야 한다."""
    monkeypatch.setattr(settings, "session_idle_timeout_minutes", 10)  # 유휴시간은 넉넉하게
    monkeypatch.setattr(settings, "session_normal_ttl_minutes", 3 / 60)  # 절대타임아웃 3초
    monkeypatch.setattr(settings, "session_activity_touch_interval_seconds", 0)  # 매번 즉시 갱신되게

    headers = _login_as_admin(client)
    headers = {**headers, "X-User-Activity": "true"}

    ok_count = 0
    final_status = None
    start = time.time()
    while time.time() - start < 5:
        response = client.get("/api/admin/employees", headers=headers)
        final_status = response.status_code
        if response.status_code != 200:
            break
        ok_count += 1
        time.sleep(0.4)  # 유휴시간(10분)보다 훨씬 짧은 간격으로 계속 활동

    assert ok_count >= 2, "절대타임아웃 전에는 계속된 활동으로 정상 응답이 나와야 한다"
    assert final_status == 401, "계속 활동 중이어도 절대 타임아웃이 지나면 결국 끊겨야 한다"
