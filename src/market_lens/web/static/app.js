/* SSE 流式工具：mlStream(url, {onStep, onThink, onAnswer, onDone, onError, resume, onGone})
   DSH 风格：step=阶段 chip，think=思考链增量，answer=正文增量。
   - 服务端每帧带自增 seq；提供 resume(seq)→url 时，连接异常断开自动从断点续传
     （页面切回、网络闪断、服务空闲断连都不会丢内容、不会报 network error）。
   - 收到 404（会话不在了，如服务重启）触发 onGone，由调用方引导到历史记录。
   - 流被过早关闭且无法续传时才报错；正常 done/error 事件正常结束。 */
window.mlStream = function (url, handlers) {
  var acc = { answer: "", think: "" };
  var seq = 0, finished = false, retries = 0, connecting = false;

  function dispatch(line) {
    if (line.indexOf("data: ") !== 0) return;   // 心跳注释行等自动忽略
    var ev;
    try { ev = JSON.parse(line.slice(6)); } catch (e) { return; }
    if (ev.seq) seq = ev.seq;
    if (ev.type === "think") { acc.think += ev.text; handlers.onThink && handlers.onThink(ev.text, acc); }
    else if (ev.type === "answer") { acc.answer += ev.text; handlers.onAnswer && handlers.onAnswer(ev.text, acc); }
    else if (ev.type === "step") { handlers.onStep && handlers.onStep(ev.text, acc); }
    else if (ev.type === "done") { finished = true; handlers.onDone && handlers.onDone(ev, acc); }
    else if (ev.type === "error") { finished = true; handlers.onError && handlers.onError(ev.message, acc); }
  }

  function reconnect(errText) {
    if (finished || connecting) return;
    if (!handlers.resume || retries >= 8) {
      handlers.onError && handlers.onError(errText || "连接中断");
      return;
    }
    retries += 1;
    setTimeout(function () { connect(handlers.resume(seq)); }, 500 * retries);
  }

  function connect(u) {
    if (finished) return;
    connecting = true;
    fetch(u, { headers: { Accept: "text/event-stream" } })
      .then(function (r) {
        connecting = false;
        if (r.status === 404 && handlers.onGone) { finished = true; handlers.onGone(acc); return; }
        if (!r.ok || !r.body) throw new Error("流不可用（HTTP " + r.status + "）");
        retries = 0;   // 连接成功后重置退避计数
        var reader = r.body.getReader();
        var dec = new TextDecoder();
        var buf = "";
        (function pump() {
          reader.read().then(function (res) {
            buf += dec.decode(res.value || new Uint8Array(), { stream: true });
            var idx;
            while ((idx = buf.indexOf("\n\n")) >= 0) {
              var frame = buf.slice(0, idx);
              buf = buf.slice(idx + 2);
              frame.split("\n").forEach(dispatch);
            }
            if (!res.done) { pump(); return; }
            if (!finished) reconnect("连接被关闭");   // 服务端提前断开且无 done/error
          }).catch(function (e) { reconnect(String(e)); });
        })();
      })
      .catch(function (e) {
        connecting = false;
        reconnect(String(e));
      });
  }

  connect(url);
  return acc;
};

/* 仪表盘图表渲染（ECharts CDN；离线时优雅降级为提示） */
(function () {
  var el = document.getElementById("dash-data");
  if (!el) return;
  var data;
  try { data = JSON.parse(el.textContent); } catch (e) { return; }

  if (typeof echarts === "undefined") {
    document.querySelectorAll(".chart").forEach(function (c) {
      c.innerHTML = '<div style="color:#999;padding:40px;text-align:center">图表需要联网加载 ECharts CDN（数据统计仍在上方卡片中）</div>';
    });
    return;
  }

  function base(elid, opt) {
    var dom = document.getElementById(elid);
    if (!dom) return;
    var chart = echarts.init(dom);
    chart.setOption(Object.assign({
      tooltip: {}, grid: { left: 90, right: 20, top: 10, bottom: 24 },
      textStyle: { fontFamily: "-apple-system, PingFang SC, sans-serif", fontSize: 12 },
    }, opt));
    window.addEventListener("resize", function () { chart.resize(); });
  }

  base("chart-skills", {
    xAxis: { type: "value" },
    yAxis: { type: "category", data: data.skills.map(function (d) { return d[0]; }).reverse() },
    series: [{ type: "bar", data: data.skills.map(function (d) { return d[1]; }).reverse(),
               itemStyle: { color: "#3565e0", borderRadius: [0, 4, 4, 0] }, barMaxWidth: 18 }],
  });

  base("chart-salary", {
    xAxis: { type: "category", data: data.salary.map(function (d) { return d[0]; }) },
    yAxis: { type: "value" },
    series: [{ type: "bar", data: data.salary.map(function (d) { return d[1]; }),
               itemStyle: { color: "#16a34a", borderRadius: [4, 4, 0, 0] }, barMaxWidth: 34 }],
  });

  base("chart-city", {
    series: [{ type: "pie", radius: ["38%", "68%"], center: ["50%", "50%"],
               data: data.city.map(function (d) { return { name: d[0], value: d[1] }; }),
               label: { fontSize: 12 } }],
  });

  base("chart-wlb", {
    xAxis: { type: "category", data: data.wlb.map(function (d) { return d[0]; }) },
    yAxis: { type: "value" },
    series: [{ type: "bar", data: data.wlb.map(function (d) { return d[1]; }),
               itemStyle: { color: "#d97706", borderRadius: [4, 4, 0, 0] }, barMaxWidth: 34 }],
  });
})();
