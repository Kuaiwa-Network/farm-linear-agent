"use strict";
// FarmBot's read-only status page. Every value from /api/status reaches the page through textContent or a
// text node; links are made only for Linear and GitHub URLs. The JSON stays language-neutral; labels live here.
(() => {
  const POLL_MS = 5000;
  const BUSY_NOTICE_SECONDS = 5;
  const SAFE_LINK = /^https:\/\/(linear\.app|github\.com)\/\S*$/;
  const VERDICT = {
    ok: ["正常", "ok"], attention: ["需要关注", "warn"], starting: ["正在启动", "info"],
    unresponsive: ["无响应", "bad"], stopped: ["已停止", "bad"], unknown: ["未知", "neutral"],
  };
  const SKILL = {fix: "修复", chat: "对话"};
  const STATE = {
    queued: ["排队中", "neutral"], launching: ["正在启动", "info"], switching_repo: ["切换仓库", "info"],
    retry_wait: ["等待重试", "neutral"], running: ["处理中", "info"], awaiting_input: ["等待回复", "warn"],
    awaiting_resource: ["等待 Unity", "neutral"], waiting_for_recovery: ["等待 Unity 修复", "warn"],
  };
  const OUTCOME = {
    delivered: ["已交付", "ok"], no_change: ["无需改动", "ok"], blocked: ["受阻", "warn"],
    failed: ["失败", "bad"], cancelled: ["已停止", "neutral"],
  };
  const LOOP = {
    receive: "接收", schedule: "调度", pool: "Unity 池", lifecycle: "状态同步", progress: "进度汇报",
    resource_recovery: "资源恢复",
  };
  const WORKER = {
    alive: ["worker 运行中", "ok"], renewal_overdue: ["续约逾期", "warn"], untracked: ["未被服务跟踪", "bad"],
    lease_expired: ["租约已过期", "bad"],
  };
  const SLOT = {
    idle_closed: ["空闲", "neutral"], idle_open: ["空闲（编辑器已打开）", "ok"], switching: ["切换中", "info"],
    interactive_busy: ["交互测试中", "info"], batch_busy: ["批量测试中", "info"], held: ["已隔离", "bad"],
  };
  // Each label receives the attention item and the rendered work document, whose slots may be null.
  const ATTENTION = {
    slot_held: (a, work) => {
      const slot = ((work && work.slots) || []).find((candidate) => candidate.slot_id === a.subject);
      // An exhausted recovery means the controller has given up: a person must act, nothing is repairing.
      return slot && slot.recovery && slot.recovery.state === "exhausted"
        ? `${a.subject} 已隔离，自动修复已放弃，需要人工处理`
        : `${a.subject} 已隔离，控制器正在自动修复${a.count ? `（第 ${a.count} 次）` : ""}`;
    },
    slot_without_reservation: (a) => `${a.subject} 显示忙碌，但没有对应的占用记录`,
    lease_expired: (a) => `${a.subject || "一项工作"} 的租约已过期，worker 可能已退出`,
    cleanup_pending: (a) => `${a.subject || "一项工作"} 的清理尚未完成，已保留恢复证据`,
    issue_status_error: (a) => `${a.subject || "一个 issue"} 的 Linear 状态读取连续失败 ${a.count} 次`,
    reservation_cancel_pending: (a) => `${a.subject || "一项工作"} 的 Unity 占用正在取消，尚未完成`,
    loop_erroring: (a) => `${LOOP[a.subject] || a.subject} 循环连续出错 ${a.count} 次`,
    loop_stalled: (a) => `${LOOP[a.subject] || a.subject} 循环单次运行时间过长`,
    webhook_rejected: (a) => `最近有 webhook 被拒绝（签名、时间或身份不符；启动以来共 ${a.count} 个）`,
    receiver_unreachable: () => "接收器 /health 没有响应，但服务心跳正常",
    heartbeat_stale: () => "服务心跳已过期，但 /health 仍有响应",
    heartbeat_unreadable: () => "服务心跳文件无法读取",
    renewal_overdue: (a) => `${a.subject || "一项工作"} 的 worker 超过预期时间没有续约，可能卡住了`,
    worker_untracked: (a) => `${a.subject || "一项工作"} 显示处理中，但服务没有在管理对应的 worker 进程`,
  };

  let doc = null;
  let fetchedAt = 0;
  let lastGood = null;
  let failingSince = null;

  const byId = (id) => document.getElementById(id);

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function cell(className, ...parts) {
    const node = el("div", className);
    for (const part of parts) {
      if (part !== null && part !== undefined && part !== "") node.append(part);  // strings become text nodes
    }
    return node;
  }

  function row(className, ...cells) {
    const node = el("div", `row ${className}`);
    node.append(...cells);
    return node;
  }

  function pill(label, tone) {
    return el("span", `pill ${tone || "neutral"}`, label);
  }

  function link(url, text) {
    if (typeof url !== "string" || !SAFE_LINK.test(url)) return el("span", "", text);
    const anchor = el("a", "link", text);
    anchor.href = url;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    return anchor;
  }

  const hostNow = () => doc.generated_at + (Date.now() - fetchedAt) / 1000;

  function duration(seconds) {
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return `${s} 秒`;
    if (s < 3600) return `${Math.floor(s / 60)} 分钟`;
    if (s < 86400) return `${Math.floor(s / 3600)} 小时 ${Math.floor((s % 3600) / 60)} 分钟`;
    return `${Math.floor(s / 86400)} 天 ${Math.floor((s % 86400) / 3600)} 小时`;
  }

  function ago(t) {
    if (typeof t !== "number") return "";
    const seconds = hostNow() - t;
    return seconds < 5 ? "刚刚" : `${duration(seconds)}前`;
  }

  function since(t) {
    return typeof t === "number" ? duration(hostNow() - t) : "";
  }

  function clock(t) {
    if (typeof t !== "number") return "";
    const date = new Date(t * 1000);
    const time = date.toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit", hour12: false});
    return date.toDateString() === new Date().toDateString()
      ? time : `${date.getMonth() + 1}月${date.getDate()}日 ${time}`;
  }

  function prLinks(prs) {
    const node = el("div", "prs");
    prs.forEach((pr, index) => {
      if (index) node.append(" · ");
      node.append(link(pr.url, `PR ${pr.label}`));
    });
    return node;
  }

  function renderHeader() {
    // A lost connection overrides the last verdict: the tab title must not keep saying 正常.
    const lost = failingSince !== null;
    const [label, tone] = lost ? ["连接中断", "bad"] : VERDICT[doc.verdict] || VERDICT.unknown;
    const from = lost ? failingSince / 1000 : doc.verdict !== "ok" ? doc.verdict_since : null;
    const bot = doc.instance.bot_name || "FarmBot";
    const beat = doc.service.heartbeat;
    document.title = `${label} · ${bot} 状态`;
    byId("title").textContent = `${bot} 状态`;
    const parts = [doc.instance.environment, doc.instance.host];
    if (beat.revision) parts.push(`版本 ${beat.revision.slice(0, 7)}${beat.dirty ? "（有未提交改动）" : ""}`);
    if (beat.state === "fresh" && beat.phase === "serving") parts.push(`已运行 ${since(beat.started_at)}`);
    byId("subtitle").textContent = parts.filter(Boolean).join(" · ");
    const verdict = byId("verdict");
    verdict.className = `pill ${tone}`;
    verdict.textContent = typeof from === "number" ? `${label}（自 ${clock(from)}）` : label;
    byId("updated").textContent = `更新于 ${ago(doc.generated_at)} · 每 5 秒刷新`;
  }

  function renderCounts(work) {
    const tiles = [["处理中", work.counts.running], ["排队", work.counts.queued],
                   ["等待回复", work.counts.awaiting_input], ["等待 Unity", work.counts.awaiting_resource]];
    byId("counts").replaceChildren(...tiles.map(([label, value]) =>
      cell("tile", el("div", "muted", label), el("div", "number", value))));
  }

  function renderAttention(work) {
    const items = doc.attention || [];
    byId("attention").hidden = items.length === 0;
    byId("attention-count").textContent = items.length ? ` · ${items.length}` : "";
    byId("attention-rows").replaceChildren(...items.map((item) =>
      row("pair", cell("main", (ATTENTION[item.code] || (() => item.code))(item, work)),
          cell("side muted", item.since ? since(item.since) : ""))));
  }

  function loopPill(loop) {
    const name = LOOP[loop.name] || loop.name;
    const busyFor = typeof loop.started_at === "number" ? hostNow() - loop.started_at : 0;
    if (loop.state === "erroring") return pill(`${name} 出错 ${loop.error_type || ""}`.trim(), "bad");
    if (loop.state === "stalled") return pill(`${name} 已运行 ${duration(busyFor)}`, "warn");
    if (loop.state === "busy" && busyFor >= BUSY_NOTICE_SECONDS) return pill(`${name} 忙碌 ${duration(busyFor)}`, "info");
    return pill(`${name} ${ago(loop.finished_at ?? loop.started_at)}`, "ok");
  }

  function renderService() {
    const service = doc.service;
    const rows = [];
    const health = service.health;
    const detail = health.ok ? `${health.latency_ms} ms` : health.error_type || (health.status ? `HTTP ${health.status}` : "");
    rows.push(row("two", cell("label muted", "接收器 /health"),
      cell("main", health.ok ? pill("正常", "ok") : pill("无响应", "bad"), " ", el("span", "muted", detail))));
    const beat = service.heartbeat;
    const stopped = beat.phase === "stopped" ? `已停止（${clock(beat.stopped_at)}）` : null;
    const beatText = {
      fresh: stopped || `${ago(beat.written_at)}写入`,
      stale: stopped || `已过期（最后 ${clock(beat.written_at)}）`,
      missing: "此版本未提供", unreadable: "无法读取",
    }[beat.state] || "";
    rows.push(row("two", cell("label muted", "服务心跳"), cell("main", beatText)));
    if (service.loops.length) {
      const pills = el("div", "pills");
      pills.append(...service.loops.map(loopPill));
      rows.push(row("two", cell("label muted", "后台循环"), cell("main", pills)));
    } else if (beat.state === "missing") {
      rows.push(row("two", cell("label muted", "后台循环"), cell("main muted", "此版本未提供")));
    }
    const hooks = service.webhooks;
    const bits = [];
    if (hooks) bits.push(hooks.last_at ? `最近收到 ${ago(hooks.last_at)}${hooks.last_type ? `（${hooks.last_type}）` : ""}` : "启动以来尚未收到");
    if (service.agent_event_at) bits.push(`会话事件 ${ago(service.agent_event_at)}`);
    if (hooks) bits.push(`拒绝 ${hooks.counts.rejected}`);
    if (bits.length) rows.push(row("two", cell("label muted", "Webhook"), cell("main", bits.join(" · "))));
    if (service.linear) {
      const read = service.linear.last_ok_at ? `状态读取成功 ${ago(service.linear.last_ok_at)}` : "尚无成功的状态读取";
      rows.push(row("two", cell("label muted", "Linear"), cell("main", `${read} · 失败 ${service.linear.failing_issues}`)));
    }
    byId("service-rows").replaceChildren(...rows);
  }

  function renderActive(work) {
    const target = byId("active-rows");
    if (!work.active.length) return target.replaceChildren(el("div", "empty muted", "目前没有进行中的工作"));
    target.replaceChildren(...work.active.map((job) => {
      const [label, tone] = STATE[job.display_state] || [job.display_state, "neutral"];
      const meta = [SKILL[job.skill] || job.skill, job.repo, job.stage && `阶段 ${job.stage}`].filter(Boolean).join(" · ");
      const main = cell("main", el("div", "title", job.title || ""), el("div", "muted", meta));
      if (job.prs.length) main.append(prLinks(job.prs));
      let state = label;
      if (job.queue_position) state += ` · 第 ${job.queue_position} 位`;
      if (job.retry_at) state += ` · ${clock(job.retry_at)}`;
      const status = cell("state", pill(state, tone));
      const timing = cell("side muted", `${job.state === "running" ? "已处理" : "已等待"} ${since(job.state_since)}`);
      if (job.checkpoint_at) timing.append(el("div", "", `检查点 ${ago(job.checkpoint_at)}`));
      if (job.worker) {
        const [workerLabel, workerTone] = WORKER[job.worker.state] || [job.worker.state, "neutral"];
        status.append(pill(workerLabel, workerTone));
        if (job.worker.renewed_at) timing.append(el("div", "", `上次续约 ${ago(job.worker.renewed_at)}`));
      }
      return row("four", cell("ident", link(job.url, job.identifier || "?")), main, status, timing);
    }));
  }

  function recoveryCell(recovery) {
    const {attempts, max_attempts: most} = recovery;
    const counted = typeof attempts === "number";
    // Exhausted: the controller has given up automatic repair, so the cell must stand out and ask for a person.
    if (recovery.state === "exhausted") {
      return cell("side urgent", `自动修复已放弃${counted ? `（${attempts}/${most} 次）` : ""}，需要人工处理`);
    }
    if (!counted || attempts === 0) return cell("side muted", "等待修复");
    return cell("side muted", `修复中（第 ${attempts}/${most} 次）`);
  }

  function renderSlots(work) {
    const target = byId("slot-rows");
    if (work.slots === null) return target.replaceChildren(el("div", "empty muted", "此版本未提供"));
    if (!work.slots.length) return target.replaceChildren(el("div", "empty muted", "未配置 Unity 槽位"));
    const rows = work.slots.map((slot) => {
      const [label, tone] = SLOT[slot.state] || [slot.state, "neutral"];
      const detail = [slot.mode, slot.commit && `提交 ${slot.commit}`].filter(Boolean).join(" · ");
      const side = slot.recovery ? recoveryCell(slot.recovery) : cell("side", slot.holder || "");
      return row("four", cell("ident", slot.slot_id), cell("main muted", detail), cell("state", pill(label, tone)), side);
    });
    if (work.unity_queue) rows.push(row("two", cell("label muted", "排队"), cell("main", `${work.unity_queue} 项工作在等待 Unity 槽位`)));
    target.replaceChildren(...rows);
  }

  function renderRecent(work) {
    const target = byId("recent-rows");
    if (!work.recent.length) return target.replaceChildren(el("div", "empty muted", "最近 7 天没有完成的工作"));
    target.replaceChildren(...work.recent.map((job) => {
      const [label, tone] = OUTCOME[job.outcome] || [job.outcome, "neutral"];
      const main = cell("main", el("div", "title", job.title || ""), el("div", "muted", SKILL[job.skill] || job.skill || ""));
      if (job.prs.length) main.append(prLinks(job.prs));
      const state = cell("state", pill(label, tone));
      if (job.retried) state.append(el("div", "muted", "已重试"));
      return row("four", cell("ident", link(job.url, job.identifier || "?")), main, state,
                 cell("side muted", clock(job.finished_at)));
    }));
  }

  function renderBanner() {
    const messages = [];
    if (failingSince !== null) messages.push(`与监控的连接已中断（自 ${clock(failingSince / 1000)}），正在重试。`);
    if (!doc) {
      messages.push("正在连接监控服务。");
    } else if (!doc.ledger.ok) {
      const error = doc.ledger.error_type || "未知错误";
      messages.push(lastGood ? `账本暂时无法读取（${error}），工作数据停留在 ${clock(lastGood.generated_at)}。`
                             : `账本暂时无法读取（${error}）。`);
    }
    const banner = byId("banner");
    banner.hidden = messages.length === 0;
    banner.textContent = messages.join(" ");
    byId("content").classList.toggle("stale", failingSince !== null || Boolean(doc && !doc.ledger.ok));
  }

  function render() {
    if (doc) {
      const work = doc.ledger.ok ? doc : lastGood || doc;
      renderHeader();
      renderCounts(work);
      renderAttention(work);
      renderService();
      renderActive(work);
      renderSlots(work);
      renderRecent(work);
    }
    renderBanner();
  }

  async function poll() {
    try {
      const response = await fetch("/api/status", {cache: "no-store"});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      doc = await response.json();
      fetchedAt = Date.now();
      failingSince = null;
      if (doc.ledger.ok) lastGood = doc;
    } catch (error) {
      if (failingSince === null) failingSince = Date.now();
    }
    render();
    setTimeout(poll, POLL_MS);
  }

  setInterval(render, 1000);
  poll();
})();
