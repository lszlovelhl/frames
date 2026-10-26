"""自然语言对话接口：用户说人话，AI 理解意图并调用对应功能。

各个平台（飞书/微信/网页）都接这一个口子。
"""
from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    user_id: str = "anonymous"
    platform: str = "web"  # web / feishu / wechat / douyin


# 意图识别 prompt
SYSTEM_PROMPT = """你是帧间（Frames）AI 短视频拆解平台的助手。用户会用自然语言和你对话，你需要识别用户意图并提取参数。

当前支持的功能：
1. **拆解视频**：用户想上传/拆解一个视频
   - 意图码：breakdown
   - 参数：url（视频链接）
   - 触发词：拆解、分析、拆一下、帮我看看这个视频、这个视频讲什么、帮我拆
   - 示例输入："帮我拆解 https://v.douyin.com/abc123"
   - 示例输出：{"intent": "breakdown", "url": "https://v.douyin.com/abc123", "video_id": ""}

2. **查拆解结果**：用户想查某个视频的拆解进度或结果
   - 意图码：get_result
   - 参数：video_id（拆解任务 ID）
   - 触发词：结果、进度、拆完了吗、查一下、怎么样了、好了吗、拆好没
   - 示例输入："拆完了吗"
   - 示例输出：{"intent": "get_result", "url": "", "video_id": ""}
   - 注意：如果上下文里有最近的 job_id，video_id 留空，系统会自动用上下文的

3. **找相似视频**：用户想找类似的视频
   - 意图码：similar
   - 参数：video_id
   - 触发词：相似、类似、推荐、还有类似的吗、有没有差不多的
   - 示例输入："还有类似的视频吗"
   - 示例输出：{"intent": "similar", "url": "", "video_id": ""}

4. **帮助**：用户不知道能做什么，或者打招呼
   - 意图码：help
   - 参数：无
   - 触发词：你能做什么、帮助、怎么用、你好、hi、在吗
   - 示例输入："你好"
   - 示例输出：{"intent": "help", "url": "", "video_id": ""}

5. **记住偏好**：用户想让 AI 记住某个偏好或习惯
   - 意图码：remember
   - 参数：content（要记住的内容）
   - 触发词：记住、我喜欢、我偏好、以后都、别忘了、记一下
   - 示例输入："记住我喜欢口播类视频"
   - 示例输出：{"intent": "remember", "url": "", "video_id": "", "content": "我喜欢口播类视频"}

6. **查记忆**：用户想知道 AI 记住了什么
   - 意图码：recall
   - 参数：无
   - 触发词：你记住了我什么、我的偏好、你记得我什么
   - 示例输入："你都记住我什么了"
   - 示例输出：{"intent": "recall", "url": "", "video_id": ""}

7. **反馈**：用户对拆解结果不满意，提反馈
   - 意图码：feedback
   - 参数：content（反馈内容）
   - 触发词：不准、不对、不好、不满意、太差了、有问题
   - 示例输入："这个分析不准"
   - 示例输出：{"intent": "feedback", "url": "", "video_id": "", "content": "这个分析不准"}

7. **闲聊**：用户随便聊聊，不涉及具体功能
   - 意图码：chat
   - 参数：content（用户说的话）
   - 触发词：你今天怎么样、累不累、在干嘛、谢谢、哈哈
   - 示例输入："你今天怎么样"
   - 示例输出：{"intent": "chat", "url": "", "video_id": "", "content": "你今天怎么样"}

8. **回答追问**：用户回答 AI 刚才问的问题（比如想重点分析什么）
   - 意图码：followup
   - 参数：content（用户回答的内容）
   - 触发词：开头钩子、情绪曲线、段落结构、重点、关注、分析什么
   - 示例输入："开头钩子"
   - 示例输出：{"intent": "followup", "url": "", "video_id": "", "content": "开头钩子"}

只返回 JSON，不要其他内容。"""


async def _llm_intent_with_context(message: str, system_prompt: str) -> dict:
    """用 LLM 识别用户意图（带上下文）。"""
    from app.services.two_layer.pipeline import call_flash_llm
    try:
        resp = await call_flash_llm(
            prompt=message,
            system=system_prompt,
            max_tokens=200,
        )
        m = re.search(r'\{.*\}', resp, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.warning("意图识别失败：%s", e)
    return {"intent": "help", "url": "", "video_id": ""}


async def _llm_intent(message: str) -> dict:
    """用 LLM 识别用户意图。"""
    return await _llm_intent_with_context(message, SYSTEM_PROMPT)


async def _handle_breakdown(db, url, session_id) -> tuple[str, dict]:
    """处理拆解意图。返回 (回复文本, 额外数据)。"""
    from app.routers.videos import create_video
    from sqlalchemy import text as sa_text

    if not url:
        return "发个视频链接给我，我帮你拆解。支持抖音/B站/YouTube。", {}

    try:
        result = await create_video(VideoCreate(url=url), db=db)
        reply = (
            "收到！开始拆解这个视频了 🎬\n"
            "预计 3-5 分钟出结果。\n"
            "\n"
            "对了，你想重点分析什么？比如：\n"
            "• 开头钩子\n"
            "• 情绪曲线\n"
            "• 段落结构\n"
            "直接告诉我，我会在报告里特别标注。"
        )
        await db.execute(sa_text(
            "UPDATE chat_sessions SET last_job_id=:jid, updated_at=CURRENT_TIMESTAMP WHERE id=:sid"
        ), {"jid": result.get("analysis_id"), "sid": session_id})
        return reply, {"job_id": result.get("analysis_id")}
    except Exception as e:
        return f"拆解失败了：{str(e)[:100]}", {}


async def _handle_get_result(db, video_id) -> str:
    """处理查结果意图。"""
    from sqlalchemy import text as sa_text

    if not video_id:
        return "告诉我你想查哪个视频，发链接或者视频ID。"

    try:
        row = (await db.execute(text(
            "SELECT status, pct, message, script_id FROM breakdown_jobs WHERE id=:jid"
        ), {"jid": video_id})).fetchone()
        if not row:
            return "没找到这个拆解任务，确认一下 ID 对不对？"

        status, pct, msg, sid = row
        if status == "done":
            try:
                sid_str = sid.hex if hasattr(sid, 'hex') else str(sid)
                script_row = (await db.execute(sa_text(
                    "SELECT quality_score, core_idea FROM script_script WHERE id=:sid"
                ), {"sid": sid_str})).fetchone()
                segments_row = (await db.execute(sa_text(
                    "SELECT count(*) FROM script_segments WHERE script_id=:sid"
                ), {"sid": sid_str})).fetchone()
                q = script_row[0] if script_row else 0
                ci = script_row[1] if script_row else ""
                seg_count = segments_row[0] if segments_row else 0

                # 查前 3 个段落的概要
                seg_rows = (await db.execute(sa_text(
                    "SELECT start_ms, summary FROM script_segments "
                    "WHERE script_id=:sid ORDER BY start_ms LIMIT 3"
                ), {"sid": sid_str})).fetchall()

                seg_lines = []
                for i, r in enumerate(seg_rows, 1):
                    seg_lines.append(f"  {i}. {r[1][:30]}..." if r[1] else f"  {i}. 第{r[0]}ms")

                seg_text = "\n".join(seg_lines) if seg_lines else "  （暂无）"

                # 查用户偏好
                prefs_row = (await db.execute(sa_text(
                    "SELECT preferences FROM chat_sessions WHERE id=:sid"
                ), {"sid": session_id})).fetchone()
                prefs = prefs_row[0] if prefs_row else ""

                # 根据偏好调整结果展示
                highlight = ""
                if prefs:
                    highlight = f"\n⭐ 你之前说想重点分析「{prefs}」"

                # 让 LLM 解读一下结果，更有温度
                from app.services.two_layer.pipeline import call_flash_llm
                try:
                    insight = await call_flash_llm(
                        prompt=f"质量分：{q}，段落数：{seg_count}，核心想法：{ci[:100]}。用一两句话点评一下这个视频，像朋友聊天一样。",
                        system="你是短视频拆解专家，用轻松的语气点评拆解结果，短一点。",
                        max_tokens=150,
                    )
                    insight_text = f"\n💡 AI 点评：{insight.strip()}"
                except Exception:
                    insight_text = ""

                return (
                    f"✅ 拆解完成！\n"
                    f"📊 质量分：{q} · 段落数：{seg_count}\n"
                    f"🎯 核心想法：{ci[:40]}...\n"
                    f"{highlight}"
                    f"{insight_text}\n"
                    f"\n"
                    f"要不要我帮你找类似的视频？"
                )
            except Exception:
                return "✅ 拆解完成！进度：100%"
        elif status == "failed":
            # 错误自愈：根据错误原因给建议
            err_msg = msg or "未知原因"
            tips = []
            if "下载失败" in err_msg or "Unsupported URL" in err_msg:
                tips.append("链接可能不对，试试复制抖音 App 里的分享链接")
                tips.append("视频可能是私密的，检查一下权限")
            elif "素材不足" in err_msg:
                tips.append("这个视频可能没有语音，或者画面太模糊")
                tips.append("试试换个有口播的视频")
            elif "超时" in err_msg or "timeout" in err_msg.lower():
                tips.append("LLM 调用超时了，稍后再试一次")
            elif "应用重启" in err_msg:
                tips.append("刚才服务重启了，重新发一次链接吧")
            else:
                tips.append("试试换个链接")
                tips.append("或者等几分钟再试")

            tips_text = "\n".join(f"• {t}" for t in tips)
            return f"❌ 拆解失败：{err_msg}\n\n💡 建议：\n{tips_text}"
        else:
            return f"⏳ 拆解进行中...\n进度：{pct}%\n阶段：{msg or '处理中'}"
    except Exception as e:
        return f"查询失败：{str(e)[:100]}"


async def _handle_similar(db, video_id) -> str:
    """处理找相似意图。"""
    if not video_id:
        return "告诉我你想找哪个视频的相似推荐？"
    try:
        from app.routers.videos import similar_videos
        result = await similar_videos(video_id=video_id, top_k=5, db=db)
        items = result.get("items", [])
        if not items:
            return "暂无相似视频推荐（可能只有这一个已拆解视频）"
        lines = ["🎬 相似视频推荐："]
        for i, it in enumerate(items, 1):
            lines.append(f"{i}. {it.get('title', '无标题')}（相似度 {it.get('score', 0)}）")
        return "\n".join(lines)
    except Exception as e:
        return f"查询相似失败：{str(e)[:100]}"


async def _handle_remember(db, user_id, content) -> str:
    """处理记住偏好意图。带冲突解决：有相似旧记忆就更新，不新增。"""
    if not content:
        return "记住什么？比如：「记住我喜欢口播类视频」"
    try:
        import uuid
        from sqlalchemy import text as sa_text

        # 冲突解决：找相似的旧记忆（同用户，同类型，内容相似）
        # 先按精确匹配查
        old = (await db.execute(sa_text(
            "SELECT id FROM user_memory WHERE user_id=:uid AND memory_type='preference' AND content=:c"
        ), {"uid": user_id, "c": content})).fetchone()

        if not old:
            # 再按关键词查（取内容前几个字做模糊匹配，防止"我喜欢口播"和"喜欢口播类"重复存）
            key = content[:4] if len(content) >= 4 else content
            old = (await db.execute(sa_text(
                "SELECT id, content FROM user_memory WHERE user_id=:uid AND memory_type='preference' AND content LIKE :k"
            ), {"uid": user_id, "k": f"%{key}%"})).fetchone()

        if old:
            # 更新旧记忆：内容更新 + importance +1（越确认越重要）+ 刷新时间
            await db.execute(sa_text(
                "UPDATE user_memory SET content=:c, importance=MIN(importance+1, 10), "
                "updated_at=CURRENT_TIMESTAMP WHERE id=:mid"
            ), {"c": content, "mid": old[0]})
            return f"好的，我更新了记忆：{content}"
        else:
            # 新增
            mem_id = uuid.uuid4().hex
            await db.execute(sa_text(
                "INSERT INTO user_memory (id, user_id, memory_type, content, importance) "
                "VALUES (:mid, :uid, 'preference', :content, 7)"
            ), {"mid": mem_id, "uid": user_id, "content": content})
            return f"好的，我记住了：{content}"
    except Exception as e:
        return f"记忆失败：{str(e)[:100]}"


async def _handle_recall(db, user_id) -> str:
    """处理查记忆意图。"""
    try:
        from sqlalchemy import text as sa_text
        rows = (await db.execute(sa_text(
            "SELECT content, importance FROM user_memory "
            "WHERE user_id=:uid ORDER BY importance DESC"
        ), {"uid": user_id})).fetchall()

        # 查用户最近拆的视频，做用户画像
        stats_row = (await db.execute(sa_text(
            "SELECT count(*) FROM breakdown_jobs bj "
            "JOIN chat_sessions cs ON bj.id = cs.last_job_id "
            "WHERE cs.user_id=:uid AND bj.status='done'"
        ), {"uid": user_id})).fetchone()
        done_count = stats_row[0] if stats_row else 0

        if not rows and done_count == 0:
            return "我还没记住什么，你可以说「记住我喜欢口播类」告诉我。"

        lines = ["🧠 我对你的了解："]
        if done_count > 0:
            lines.append(f"• 你已经拆了 {done_count} 个视频")
        for r in rows:
            lines.append(f"• {r[0]}")

        # 简单画像
        if done_count >= 5:
            lines.append("\n💡 我猜你是做短视频的，对吧？")
        elif done_count >= 1:
            lines.append("\n💡 看来你对短视频拆解感兴趣？")

        return "\n".join(lines)
    except Exception as e:
        return f"查询记忆失败：{str(e)[:100]}"


async def _handle_feedback(db, user_id, content) -> str:
    """处理用户反馈意图。"""
    if not content:
        return "哪里不满意？告诉我具体哪里不对，我下次改进。"
    try:
        import uuid
        from sqlalchemy import text as sa_text
        mem_id = uuid.uuid4().hex
        await db.execute(sa_text(
            "INSERT INTO user_memory (id, user_id, memory_type, content, importance) "
            "VALUES (:mid, :uid, 'feedback', :content, 8)"
        ), {"mid": mem_id, "uid": user_id, "content": f"用户反馈：{content}"})
        return f"收到！我记住了：{content}\n下次我会注意的。"
    except Exception as e:
        return f"记录失败：{str(e)[:100]}"


async def _handle_chat(content: str) -> str:
    """闲聊，用本地 qwen3.5 生成，免费又快。"""
    import httpx
    try:
        # 调本地 Ollama
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://127.0.0.1:11434/api/generate",
                json={
                    "model": "qwen3.5:latest",
                    "prompt": f"你是帧间 AI 助手，一个亲切的短视频拆解伙伴。用户说：「{content}」。用轻松友好的语气回复，一两句话，顺便提一句你能帮他拆解视频。",
                    "stream": False,
                    "options": {"temperature": 0.7, "max_tokens": 100},
                },
                timeout=30,
            )
            data = resp.json()
            return data.get("response", "").strip()
    except Exception:
        # 兜底：本地模型没跑就用写死的
        if any(w in content for w in ["你好", "hi", "hello"]):
            return "你好呀！我是帧间 AI 助手 🤖\n有什么我能帮你的？发个视频链接试试。"
        return "嗯，我在听。或者直接发个视频链接，我帮你拆解分析。"


async def _handle_followup(db, session_id, user_id, content) -> str:
    """处理用户对 AI 追问的回答。"""
    from sqlalchemy import text as sa_text

    # 把用户的回答存到 session 的 preferences 里
    try:
        await db.execute(sa_text(
            "UPDATE chat_sessions SET preferences=:p, updated_at=CURRENT_TIMESTAMP WHERE id=:sid"
        ), {"p": content, "sid": session_id})
    except Exception:
        pass

    return (
        f"好的，我记住了：你想重点分析「{content}」\n"
        f"拆解完成后我会在报告里特别标注这部分。\n"
        f"\n"
        f"现在拆解应该快好了，稍后问我「拆完了吗」就能看结果。"
    )


async def _handle_help() -> str:
    """处理帮助意图。"""
    return (
        "我是帧间 AI 短视频拆解助手 🤖\n"
        "\n"
        "你可以：\n"
        "1. 发视频链接，我帮你拆解分析\n"
        "2. 问我「拆完了吗」，查进度\n"
        "3. 问我「还有类似的吗」，找相似视频\n"
        "4. 说「记住我喜欢口播类」，我记住你的偏好\n"
        "\n"
        "试试发个抖音/B站链接？"
    )



@router.post("/api/chat")
async def chat(
    req: ChatRequest,
    db: AsyncSession = Depends(get_session),
):
    """自然语言对话入口。各个平台都接这里。"""
    message = req.message.strip()
    if not message:
        return {"reply": "你想说什么？发个视频链接，或者问我拆解结果。"}

    user_id = req.user_id
    platform = req.platform

    # 1. 先做意图识别（纯 LLM，不占 db）
    intent_data = await _llm_intent(message)
    intent = intent_data.get("intent", "help")
    url = intent_data.get("url", "")
    video_id = intent_data.get("video_id", "")

    logger.info("chat: intent=%s, url=%s, video_id=%s", intent, url, video_id)

    # 2. 再开 db 处理上下文和操作
    from sqlalchemy import text as sa_text

    # 兜底：如果 session 状态坏了，先 rollback
    try:
        await db.rollback()
    except Exception:
        pass

    # 找最新 session
    sess_row = (await db.execute(sa_text(
        "SELECT id, last_video_id, last_job_id FROM chat_sessions "
        "WHERE user_id=:uid ORDER BY updated_at DESC LIMIT 1"
    ), {"uid": user_id})).fetchone()

    if sess_row:
        session_id, last_video, last_job = sess_row
        # 如果意图是 get_result/similar，但没提取到 video_id，用上下文的
        if not video_id:
            video_id = last_job or last_video
    else:
        session_id = __import__("uuid").uuid4().hex
        await db.execute(sa_text(
            "INSERT INTO chat_sessions (id, user_id, platform) VALUES (:sid, :uid, :p)"
        ), {"sid": session_id, "uid": user_id, "p": platform})
        last_video, last_job = None, None

    # 存用户消息
    msg_id = __import__("uuid").uuid4().hex
    await db.execute(sa_text(
        "INSERT INTO chat_messages (id, session_id, role, content) VALUES (:mid, :sid, 'user', :content)"
    ), {"mid": msg_id, "sid": session_id, "content": message})

    # 2. 根据意图调用对应 handler
    handlers = {
        "breakdown": lambda: _handle_breakdown(db, url, session_id),
        "get_result": lambda: _handle_get_result(db, video_id),
        "similar": lambda: _handle_similar(db, video_id),
        "remember": lambda: _handle_remember(db, user_id, intent_data.get("content", "")),
        "recall": lambda: _handle_recall(db, user_id),
        "feedback": lambda: _handle_feedback(db, user_id, intent_data.get("content", "")),
        "chat": lambda: _handle_chat(intent_data.get("content", "")),
        "followup": lambda: _handle_followup(db, session_id, user_id, intent_data.get("content", "")),
        "help": _handle_help,
    }
    handler = handlers.get(intent, _handle_help)
    result = await handler()

    if isinstance(result, tuple):
        reply_text, extra = result
    else:
        reply_text, extra = result, {}

    # 3. 存 AI 回复到历史
    ai_msg_id = __import__("uuid").uuid4().hex
    await db.execute(sa_text(
        "INSERT INTO chat_messages (id, session_id, role, content, intent) VALUES (:mid, :sid, 'assistant', :content, :intent)"
    ), {"mid": ai_msg_id, "sid": session_id, "content": reply_text, "intent": intent})

    # 3.5 自动记忆：从用户消息中提取视频类型偏好
    try:
        import re as _re
        types = ["口播", "剧情", "vlog", "测评", "教程", "带货", "美食", "旅行", "知识", "情感"]
        found = [t for t in types if t in message]
        if found and intent != "remember":
            # 检查是否已经记住过
            already = (await db.execute(sa_text(
                "SELECT id FROM user_memory WHERE user_id=:uid AND content LIKE :c"
            ), {"uid": user_id, "c": f"%{found[0]}%"})).fetchone()
            if not already:
                import uuid
                mem_id = uuid.uuid4().hex
                await db.execute(sa_text(
                    "INSERT INTO user_memory (id, user_id, memory_type, content, importance) "
                    "VALUES (:mid, :uid, 'preference', :content, 5)"
                ), {"mid": mem_id, "uid": user_id, "content": f"关注{found[0]}类视频"})
                logger.info("自动记忆：用户关注 %s 类视频", found[0])
    except Exception as e:
        logger.warning("自动记忆失败（不影响主流程）：%s", e)

    # 4. 更新 session 时间
    await db.execute(sa_text(
        "UPDATE chat_sessions SET updated_at=CURRENT_TIMESTAMP WHERE id=:sid"
    ), {"sid": session_id})

    await db.commit()

    # 4.5 记忆结晶（Auto-Dream）：异步后台跑，24 小时一次，不阻塞响应
    try:
        import asyncio
        from app.services.memory_dream import run_dream_background
        asyncio.create_task(run_dream_background())
        logger.info("已触发记忆结晶后台任务")
    except Exception as e:
        logger.warning("触发记忆结晶失败（不影响主流程）：%s", e)

    return {"reply": reply_text, **extra}


# 延迟导入避免循环依赖
from app.routers.videos import VideoCreate
