#!/bin/bash
# 帧间夜间自动监控脚本
# 每5分钟检查一次：服务状态、采集队列、失败任务、错误日志

LOG_FILE="/Users/zhuolittlelong/Projects/frames/backend/data/logs/night_monitor.log"
FRAMES_DIR="/Users/zhuolittlelong/Projects/frames"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log "=== 夜间监控启动 ==="

while true; do
    # 1. 检查服务是否活着
    if ! curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/ | grep -q "200"; then
        log "⚠️ 服务无响应，尝试重启..."
        cd "$FRAMES_DIR"
        ./run/stop 2>/dev/null
        sleep 3
        FRAMES_PIPELINE=single ./run/start --no-browser &
        sleep 10
        if curl -s -o /dev/null http://127.0.0.1:8000/; then
            log "✅ 服务重启成功"
        else
            log "❌ 服务重启失败"
        fi
    fi

    # 2. 检查活跃任务
    ACTIVE=$(curl -s http://127.0.0.1:8000/api/analyses/active 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    items = d.get('items', d) if isinstance(d, dict) else d
    running = [t for t in items if t.get('status') not in ('done', 'failed', 'error')]
    failed = [t for t in items if t.get('status') in ('failed', 'error')]
    print(f'running={len(running)} failed={len(failed)} total={len(items)}')
    for t in running[:3]:
        print(f'  RUNNING: {t.get(\"title\",\"?\")[:40]} | {t.get(\"progress\",{}).get(\"message\",\"\")}')
except Exception as e:
    print(f'error={e}')
" 2>/dev/null)
    log "任务状态: $ACTIVE"

    # 3. 检查最近错误
    RECENT_ERRORS=$(tail -100 "$FRAMES_DIR/backend/data/logs/uvicorn.log" 2>/dev/null | grep -i "error\|exception\|traceback" | tail -3)
    if [ -n "$RECENT_ERRORS" ]; then
        log "最近错误:"
        echo "$RECENT_ERRORS" | while read line; do
            log "  $line"
        done
    fi

    # 4. 检查采集队列
    QUEUE=$(curl -s http://127.0.0.1:8000/api/analyses/active 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    items = d.get('items', d) if isinstance(d, dict) else d
    running = [t for t in items if t.get('status') not in ('done', 'failed', 'error')]
    failed = [t for t in items if t.get('status') in ('failed', 'error')]
    print(f'运行中: {len(running)}, 失败: {len(failed)}')
    for t in running[:3]:
        print(f'  -> {t.get(\"title\",\"?\")[:35]} | {t.get(\"progress\",{}).get(\"message\",\"\")[:40]}')
except Exception as e:
    print(f'无法获取: {e}')
" 2>/dev/null)
    log "任务状态: $QUEUE"

    # 5. 如果没有运行中的任务，手动刷新热榜并加入新视频
    RUNNING_COUNT=$(echo "$QUEUE" | grep -o "运行中: [0-9]*" | grep -o "[0-9]*")
    if [ "$RUNNING_COUNT" = "0" ] || [ -z "$RUNNING_COUNT" ]; then
        log "无运行中任务，刷新热榜..."
        curl -s -X POST http://127.0.0.1:8000/api/collection/hotlist/refresh > /dev/null 2>&1
        sleep 5
    fi

    log "---"
    sleep 300  # 5分钟检查一次
done
