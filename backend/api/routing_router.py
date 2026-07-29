import os
import httpx
from fastapi import APIRouter, status, HTTPException
from fastapi.responses import JSONResponse

from backend.api.schemas import RoutePlanRequest, RoutePlanResponse

ROUTING_BASE = os.getenv("ROUTING_BASE", "http://svc-routing:9101")

router = APIRouter()

@router.post("/route", response_model=RoutePlanResponse, status_code=status.HTTP_200_OK)
def create_route_plan(req: RoutePlanRequest):
    """
    経路計画エンドポイント。
    Routingサービスに直接リクエストを転送し、同期的に結果を返す。
    """
    try:
        # Routingサービスへのリクエストボディを作成
        # APIスキーマ(SpotPick)とroutingサービス(Waypoint)でspot_idの有無が違うため変換
        routing_payload = {
            "language": req.language,
            "origin": req.origin.model_dump(),
            "waypoints": [{"spot_id": wp.spot_id} for wp in req.waypoints],
            "return_to_origin": req.return_to_origin,
            # car_to_trailhead は常にTrueを渡す（必要に応じてリクエストに追加も可能）
            "car_to_trailhead": True,
        }
        
        # HTTPクライアントでRoutingサービスを呼び出す
        with httpx.Client(timeout=60) as client:
            res = client.post(f"{ROUTING_BASE}/route", json=routing_payload)
            # Routingサービス側でエラーがあれば、それをクライアントに転送する
            res.raise_for_status()
        
        # 成功した場合は、レスポンスボディをそのまま返す
        return JSONResponse(status_code=res.status_code, content=res.json())

    except httpx.HTTPStatusError as e:
        # Routingサービスが4xx, 5xxを返した場合
        # そのエラー内容をそのままフロントエンドに伝える
        detail = e.response.json().get("detail", "Error from routing service")
        raise HTTPException(status_code=e.response.status_code, detail=detail)
    except Exception as e:
        # 接続エラーなど、その他の予期せぬエラー
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")