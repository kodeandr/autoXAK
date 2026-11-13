import pytest


@pytest.mark.asyncio
async def test_cpa_funnel_end_to_end(async_client):
    """Проверка создания гостевой сессии, дашборда и отправки CPA-заявки."""
    guest_res = await async_client.post("/api/v1/auth/guest")
    assert guest_res.status_code == 200
    guest_data = guest_res.json()
    token = guest_data.get("token") or guest_data.get("access_token")
    user_id = guest_data["user_id"]
    headers = {"Authorization": f"Bearer {token}"}

    dash_res = await async_client.get(f"/api/v1/users/{user_id}/dashboard", headers=headers)
    assert dash_res.status_code == 200

    claim_res = await async_client.post(
        "/api/v1/cpa/claim",
        json={
            "user_id": user_id,
            "partner_name": "FIT Service",
            "service_type": "oil_change",
            "phone": "+79991234567",
        },
        headers=headers,
    )
    assert claim_res.status_code in (200, 422)
