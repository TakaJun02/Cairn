#!/bin/bash

# --- 設定 ---
API_HOST="http://localhost:8080"
LANGUAGE="ja"
RETURN_TO_ORIGIN=true

ORIGIN_LAT=39.39313041426191
ORIGIN_LON=140.07357654611906
WAYPOINTS_JSON='[
  {"spot_id": "spot_007"},
  {"spot_id": "spot_004"}
]'

# --- 1. Routing APIを叩いて経路情報を取得 ---
echo "1. Routing APIを呼び出しています..."
read -r -d '' ROUTE_PAYLOAD <<EOF
{
  "language": "${LANGUAGE}",
  "origin": {
    "lat": ${ORIGIN_LAT},
    "lon": ${ORIGIN_LON}
  },
  "waypoints": ${WAYPOINTS_JSON},
  "return_to_origin": ${RETURN_TO_ORIGIN}
}
EOF

ROUTE_RESPONSE=$(curl -s -X POST "${API_HOST}/api/route" \
  -H "Content-Type: application/json" \
  -d "${ROUTE_PAYLOAD}")

# エラーチェック
if [ -z "$ROUTE_RESPONSE" ] || ! echo "$ROUTE_RESPONSE" | jq . &>/dev/null; then
  echo "Routing APIからのレスポンス取得に失敗しました。"
  exit 1
fi

echo "Routing APIから経路情報を取得しました。"
# echo "$ROUTE_RESPONSE" | jq . # 中間結果を確認したい場合はコメントを外す

# --- 2. NAV APIを叩くためのリクエストボディを生成 ---
echo "2. NAV APIへのリクエストを生成しています..."
NAV_REQUEST=$(echo "$ROUTE_RESPONSE" | jq -c "{
    language: \"${LANGUAGE}\",
    buffer: {car: 300, foot: 10},
    route: .feature_collection,
    polyline: .polyline,
    segments: .segments,
    legs: .legs,
    waypoints_info: .waypoints_info
}")

NAV_JSON_FILE=$(mktemp)
echo "$NAV_REQUEST" > "$NAV_JSON_FILE"

# --- 3. NAV APIを叩いてガイダンス生成タスクを開始 ---
echo "3. NAV APIを呼び出し、ガイダンス生成タスクを開始します..."
TASK_ACCEPTED_RESPONSE=$(curl -s -X POST "${API_HOST}/api/nav/plan" \
  -H "Content-Type: application/json" \
  --data-binary "@${NAV_JSON_FILE}")

TASK_ID=$(echo "$TASK_ACCEPTED_RESPONSE" | jq -r .task_id)

if [ -z "$TASK_ID" ] || [ "$TASK_ID" == "null" ]; then
    echo "NAVタスクの開始に失敗しました。"
    echo "レスポンス: $TASK_ACCEPTED_RESPONSE"
    exit 1
fi

echo "タスクを開始しました。Task ID: ${TASK_ID}"
echo "結果を取得するためにポーリングを開始します..."

# --- 4. タスクの結果をポーリング ---
while true; do
  TASK_STATUS_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" "${API_HOST}/api/nav/plan/tasks/${TASK_ID}")
  
  if [ "$TASK_STATUS_RESPONSE" -eq 200 ]; then
    echo ""
    echo "タスクが完了しました！最終レスポンス:"
    curl -s "${API_HOST}/api/nav/plan/tasks/${TASK_ID}" | jq 'keys'
    break
  elif [ "$TASK_STATUS_RESPONSE" -eq 202 ]; then
    echo -n "."
    sleep 5
  else
    echo ""
    echo "タスクの実行中にエラーが発生しました。ステータスコード: ${TASK_STATUS_RESPONSE}"
    curl -s "${API_HOST}/api/nav/plan/tasks/${TASK_ID}" | jq .
    break
  fi
done

rm -f "$NAV_JSON_FILE"
