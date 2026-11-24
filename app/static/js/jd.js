/* ===========================================================
 * 九豆高质量数据资产平台 · 前端核心库（H5 + jQuery，无 Vue）
 * 约定：
 *  1) 所有消息提示一律使用遮罩弹窗（JD.alert / JD.confirm），不使用顶部 info 条；
 *  2) 所有组件只消费 CSS 变量，换肤即时生效；
 *  3) 按钮权限 = data-perm 属性 + JD.bindPerms()，后端同接口二次校验。
 * =========================================================== */
window.JD = (function ($) {
  'use strict';

  var state = {
    boot: {}, user: {}, perms: {}, theme: 'blue',
    dicts: {}, menus: [], dictLoaded: false
  };

  /* ---------------------------------------------------- 图标 */
  function icon(name, cls) {
    return '<svg class="' + (cls || '') + '" aria-hidden="true"><use href="/static/img/icons.svg#i-' +
      name + '"></use></svg>';
  }

  /* ---------------------------------------------------- 请求 */
  function req(method, url, data, opts) {
    opts = opts || {};
    return new Promise(function (resolve, reject) {
      $.ajax({
        url: url, type: method, data: data,
        contentType: method === 'GET' ? undefined : 'application/json;charset=utf-8',
        dataType: 'json',
        processData: method === 'GET',
        xhrFields: { withCredentials: true }
      }).done(function (res) {
        if (res && res.code === 0) { resolve(res.data); return; }
        if (res && res.code === 401) { top.location.href = '/login'; return; }
        var msg = (res && res.msg) || '请求失败';
        if (!opts.silent) { alert_('操作失败', msg, 'error'); }
        reject(new Error(msg));
      }).fail(function (xhr) {
        if (xhr.status === 401) { top.location.href = '/login'; return; }
        var msg = xhr.status === 0 ? '网络异常，请检查服务是否运行'
          : '请求失败（HTTP ' + xhr.status + '）';
        if (!opts.silent) { alert_('请求异常', msg, 'error'); }
        reject(new Error(msg));
      });
    });
  }

  function get(url, params, opts) {
    return req('GET', url, params, opts);
  }
  function post(url, data, opts) {
    return req('POST', url, JSON.stringify(data || {}), opts);
  }

  function upload(url, formData) {
    return new Promise(function (resolve, reject) {
      $.ajax({
        url: url, type: 'POST', data: formData, processData: false,
        contentType: false, dataType: 'json', xhrFields: { withCredentials: true }
      }).done(function (res) {
        if (res && res.code === 0) { resolve(res.data); return; }
        alert_('操作失败', (res && res.msg) || '上传失败', 'error');
        reject(new Error((res && res.msg) || '上传失败'));
      }).fail(function (xhr) {
        alert_('上传失败', 'HTTP ' + xhr.status, 'error');
        reject(new Error('upload failed'));
      });
    });
  }

  /* ---------------------------------------------------- 弹窗 */
  var maskZ = 2000;

  function dialog(opts) {
    var id = 'jdDlg' + (maskZ++);
    var widthCls = opts.width || 'jd-dialog--sm';
    var html =
      '<div class="jd-mask" id="' + id + '">' +
        '<div class="jd-dialog ' + widthCls + '">' +
          (opts.title ? '<div class="jd-dialog__hd"><h3>' + opts.title + '</h3>' +
            '<span class="close" data-act="close">' + icon('close') + '</span></div>' : '') +
          '<div class="jd-dialog__bd">' + opts.body + '</div>' +
          '<div class="jd-dialog__ft">' + opts.footer + '</div>' +
        '</div>' +
      '</div>';
    var $m = $(html).appendTo('body');
    var closed = false;
    function close(v) {
      if (closed) return;
      closed = true;
      $m.remove();
      if (opts.onClose) opts.onClose(v);
    }
    $m.on('click', function (e) {
      if (e.target === this && opts.maskClose !== false) close(false);
    });
    $m.on('click', '[data-act="close"]', function () { close(false); });
    if (opts.onOpen) opts.onOpen($m, close);
    return { $mask: $m, close: close };
  }

  function alert_(title, msg, type) {
    type = type || 'info';
    if (msg === undefined) { msg = title; title = ''; }
    var icons = { success: 'check', error: 'close', warning: 'alert', info: 'info' };
    var body =
      '<div class="jd-alert jd-alert--' + type + '">' +
        '<div class="jd-alert__icon">' + icon(icons[type] || 'info') + '</div>' +
        '<div class="jd-alert__main" style="flex:1">' +
          (title ? '<div class="jd-alert__title">' + title + '</div>' : '') +
          '<div class="jd-alert__text">' + (msg || '') + '</div>' +
        '</div>' +
      '</div>';
    return new Promise(function (resolve) {
      var d = dialog({
        body: body,
        width: 'jd-dialog--sm',
        footer: '<button class="jd-btn jd-btn--primary" data-act="ok">知道了</button>',
        onOpen: function ($m, close) {
          $m.on('click', '[data-act="ok"]', function () { close(true); resolve(true); });
          setTimeout(function () { $m.find('[data-act="ok"]').focus(); }, 30);
        },
        onClose: function (v) { if (v === false) resolve(false); }
      });
      return d;
    });
  }

  function confirm_(title, msg, opts) {
    opts = opts || {};
    var body =
      '<div class="jd-alert jd-alert--' + (opts.type || 'warning') + '">' +
        '<div class="jd-alert__icon">' + icon(opts.type === 'danger' ? 'alert' : 'info') + '</div>' +
        '<div class="jd-alert__main" style="flex:1">' +
          '<div class="jd-alert__title">' + title + '</div>' +
          '<div class="jd-alert__text">' + (msg || '') + '</div>' +
        '</div></div>';
    return new Promise(function (resolve) {
      dialog({
        body: body, width: 'jd-dialog--sm',
        footer: '<button class="jd-btn" data-act="no">取消</button>' +
          '<button class="jd-btn ' + (opts.danger ? 'jd-btn--danger' : 'jd-btn--primary') +
          '" data-act="yes">' + (opts.okText || '确定') + '</button>',
        onOpen: function ($m, close) {
          // 结果一律通过 close(v) 交回 onClose 裁决。
          // 若这里写成 close(); resolve(true)，close 内部会先触发 onClose 的 resolve(false)，
          // Promise 只能落定一次 → 用户点「确定」永远拿到 false（曾导致退出登录/删除全部失效）。
          $m.on('click', '[data-act="no"]', function () { close('no'); });
          $m.on('click', '[data-act="yes"]', function () { close('yes'); });
        },
        onClose: function (v) { resolve(v === 'yes'); }
      });
    });
  }

  /* 表单弹窗 */
  function formDialog(cfg) {
    var fields = cfg.fields || [];
    var data = cfg.data || {};
    function inputHtml(f) {
      var v = data[f.name];
      if (v === undefined || v === null) v = (f.value !== undefined ? f.value : '');
      var req = f.required ? ' <span class="req">*</span>' : '';
      var lbl = '<label class="jd-field__label">' + f.label + req + '</label>';
      var dis = f.readonly ? ' readonly' : '';
      var ph = ' placeholder="' + (f.placeholder || '') + '"';
      var h = '';
      switch (f.type) {
        case 'select':
          h = '<select class="jd-select" data-f="' + f.name + '">' +
            (f.placeholder !== false ? '<option value="">请选择</option>' : '') +
            (f.options || []).map(function (o) {
              var val = o.value === undefined ? o : o.value;
              var lab = o.label === undefined ? o : o.label;
              return '<option value="' + val + '"' + (String(v) === String(val) ? ' selected' : '') +
                '>' + lab + '</option>';
            }).join('') + '</select>';
          break;
        case 'textarea':
          h = '<textarea class="jd-textarea" data-f="' + f.name + '" rows="' + (f.rows || 3) + '"' +
            ph + dis + '>' + (v || '') + '</textarea>';
          break;
        case 'switch':
          h = '<div class="jd-switch' + (String(v) === '1' || v === true ? ' is-on' : '') +
            '" data-f="' + f.name + '" data-v="' + (String(v) === '1' || v === true ? 1 : 0) + '"></div>';
          break;
        case 'number':
          h = '<input class="jd-input" type="number" data-f="' + f.name + '" value="' + v + '"' +
            (f.step ? ' step="' + f.step + '"' : '') + ph + dis + '>';
          break;
        case 'password':
          h = '<input class="jd-input" type="password" data-f="' + f.name + '" value="' + v + '"' +
            ph + dis + ' autocomplete="new-password">';
          break;
        case 'date':
          h = '<input class="jd-input" type="date" data-f="' + f.name + '" value="' + (v || '') + '"' + dis + '>';
          break;
        case 'static':
          h = '<div class="jd-input" style="background:var(--bg-stripe);line-height:30px">' + (v || '-') + '</div>';
          break;
        default:
          h = '<input class="jd-input" data-f="' + f.name + '" value="' + (v === null ? '' : v) + '"' +
            ph + dis + '>';
      }
      return '<div class="jd-field ' + (f.span === 2 ? 'span2' : '') + '" data-wrap="' + f.name + '">' +
        lbl + h + (f.hint ? '<div class="jd-field__hint">' + f.hint + '</div>' : '') + '</div>';
    }
    var body = '<form class="jd-form-grid" onsubmit="return false">' +
      fields.map(inputHtml).join('') + '</form>';

    return new Promise(function (resolve) {
      var dlg = dialog({
        title: cfg.title || '编辑', width: cfg.width || 'jd-dialog',
        body: body,
        footer: '<button class="jd-btn" data-act="cancel">取消</button>' +
          '<button class="jd-btn jd-btn--primary" data-act="save">' + (cfg.okText || '保存') + '</button>',
        onOpen: function ($m, close) {
          /** 字段联动：onChange(fieldName, api) 里可用 api.val / api.setOptions / api.hint */
          var api = {
            $m: $m,
            val: function (n) { return $m.find('[data-f="' + n + '"]').val(); },
            set: function (n, v) { $m.find('[data-f="' + n + '"]').val(v); },
            setOptions: function (n, opts, keepValue) {
              var $s = $m.find('select[data-f="' + n + '"]');
              if (!$s.length) return;
              var cur = $s.val();
              var html = '<option value="">请选择</option>' + (opts || []).map(function (o) {
                var val = o.value === undefined ? o : o.value;
                var lab = o.label === undefined ? o : o.label;
                return '<option value="' + val + '">' + lab + '</option>';
              }).join('');
              $s.html(html);
              if (keepValue && cur) $s.val(cur);
            },
            hint: function (n, txt) {
              var $w = $m.find('[data-wrap="' + n + '"] .jd-field__hint');
              if ($w.length) $w.text(txt); else
                $m.find('[data-wrap="' + n + '"]').append('<div class="jd-field__hint">' + txt + '</div>');
            }
          };
          $m.on('change', '[data-f]', function () {
            var name = $(this).attr('data-f');
            fields.forEach(function (f) {
              if (typeof f.onChange === 'function') f.onChange(name, api);
            });
          });
          if (cfg.onOpen) cfg.onOpen(api);
          $m.on('click', '.jd-switch', function () {
            var $s = $(this);
            var on = !$s.hasClass('is-on');
            $s.toggleClass('is-on', on).attr('data-v', on ? 1 : 0);
          });
          $m.on('click', '[data-act="cancel"]', function () { close(); resolve(null); });
          $m.on('click', '[data-act="save"]', function () {
            var vals = {};
            $m.find('[data-f]').each(function () {
              var $i = $(this), k = $i.attr('data-f');
              vals[k] = $i.hasClass('jd-switch') ? Number($i.attr('data-v'))
                : ($i.attr('type') === 'number' ? ($i.val() === '' ? null : Number($i.val())) : $i.val());
            });
            var bad = null;
            fields.forEach(function (f) {
              if (f.required && (vals[f.name] === '' || vals[f.name] === null || vals[f.name] === undefined)) {
                if (!bad) bad = f;
              }
              if (!bad && f.validate) {
                var msg = f.validate(vals[f.name], vals);
                if (msg) bad = { name: f.name, msg: msg };
              }
            });
            $m.find('.jd-field__error').remove();
            if (bad) {
              var $w = $m.find('[data-wrap="' + bad.name + '"]');
              $w.append('<div class="jd-field__error">' + (bad.msg || (bad.label + '不能为空')) + '</div>');
              $w.find('input,select,textarea').focus();
              return;
            }
            var ret = cfg.onSubmit ? cfg.onSubmit(vals) : true;
            if (ret && ret.then) {
              $m.find('[data-act="save"]').prop('disabled', true).text('保存中…');
              ret.then(function (r) {
                if (r === false) {
                  $m.find('[data-act="save"]').prop('disabled', false).text(cfg.okText || '保存');
                  return;
                }
                close(); resolve(vals);
              }).catch(function () {
                $m.find('[data-act="save"]').prop('disabled', false).text(cfg.okText || '保存');
              });
            } else if (ret !== false) { close(); resolve(vals); }
          });
        },
        onClose: function () { resolve(null); }
      });
      return dlg;
    });
  }

  /* ---------------------------------------------------- 加载遮罩 */
  var loadingEl = null;
  function loading(show, text) {
    if (show) {
      if (!loadingEl) {
        loadingEl = $('<div class="jd-loading"><div class="jd-spinner"></div><span>' +
          (text || '加载中…') + '</span></div>').appendTo('body');
      }
    } else if (loadingEl) { loadingEl.remove(); loadingEl = null; }
  }

  /* ---------------------------------------------------- 权限 */
  function hasPerm(code) { return !!state.perms[code]; }

  function bindPerms(root) {
    $(root || document).find('[data-perm]').each(function () {
      var codes = ($(this).attr('data-perm') || '').split(',');
      var ok = codes.some(function (c) { return hasPerm(c.trim()); });
      if (!ok) $(this).remove();
    });
  }

  /* ---------------------------------------------------- 主题 */
  var THEMES = [
    { key: 'blue', name: '科技蓝', color: '#2f6bff' },
    { key: 'dark', name: '暗夜黑', color: '#5b8def' },
    { key: 'teal', name: '青碧', color: '#0f9b8e' },
    { key: 'violet', name: '雅紫', color: '#6d5bd0' },
    { key: 'amber', name: '暖橙', color: '#d97706' }
  ];

  function applyTheme(key, persist) {
    state.theme = key || 'blue';
    document.documentElement.setAttribute('data-theme', state.theme);
    try { localStorage.setItem('jd_theme', state.theme); } catch (e) { }
    if (persist) post('/api/auth/theme', { theme: state.theme }, { silent: true }).catch(function () { });
  }

  function initTheme() {
    var saved = state.user.pref_theme || localStorage.getItem('jd_theme') || 'blue';
    applyTheme(saved, false);
  }

  /* ---------------------------------------------------- 字典 */
  function loadDicts() {
    if (state.dictLoaded) return Promise.resolve(state.dicts);
    return get('/api/public/dicts').then(function (d) {
      state.dicts = d || {};
      state.dictLoaded = true;
      return state.dicts;
    });
  }

  function dict(code) { return state.dicts[code] || []; }

  function label(code, value) {
    var arr = dict(code);
    for (var i = 0; i < arr.length; i++) {
      if (String(arr[i].value) === String(value)) return arr[i].label;
    }
    return value === null || value === undefined || value === '' ? '-' : value;
  }

  /* ---------------------------------------------------- 格式化 */
  function fmtSize(bytes) {
    bytes = Number(bytes || 0);
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
    return (bytes / 1073741824).toFixed(2) + ' GB';
  }
  function fmtNum(n) {
    n = Number(n || 0);
    return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }
  function fmtTime(v) { return v ? String(v).slice(0, 16) : '-'; }
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /* ---------------------------------------------------- 操作功能引导区
   * 约定：列表页不在表格里放「操作列」，而是把行级操作统一收进检索条件下方的
   * 图标引导区。未选中行时行级按钮自动禁用，避免误操作。
   *   actions: [{ icon, text, perm, type:'danger|primary', needRow, sep, onClick(row) }]
   */
  function opsInner(actions, sel, hint) {
    var btns = actions.map(function (a, i) {
      if (a.sep) return '<span class="jd-ops__sep"></span>';
      var dis = a.needRow && !sel ? ' disabled' : '';
      var cls = 'jd-ops__btn' + (a.type === 'danger' ? ' jd-ops__btn--danger'
        : (a.type === 'primary' ? ' jd-ops__btn--primary' : ''));
      return '<button class="' + cls + '" data-i="' + i + '"' + dis +
        (a.perm ? ' data-perm="' + a.perm + '"' : '') +
        ' title="' + esc(a.text || '') + '">' + icon(a.icon || 'file') +
        (a.text ? '<span>' + esc(a.text) + '</span>' : '') + '</button>';
    }).join('');
    var selTxt = sel
      ? '已选：<b>' + esc(sel.__label || sel.name || sel.title || sel.dataset_name || sel.id) + '</b>'
      : (hint || '点选表格行后可执行行级操作');
    return '<div class="jd-ops__lead">' + icon('grid') + '<span>操作</span></div>' +
      '<div class="jd-ops__btns">' + btns + '</div>' +
      '<div class="jd-ops__sel">' + selTxt + '</div>';
  }

  function opsBar(cfg) {
    var $el = $(cfg.el);
    var actions = cfg.actions || [];
    var sel = null;
    function draw() {
      $el.html('<div class="jd-ops">' + opsInner(actions, sel, cfg.hint) + '</div>');
      bindPerms($el);
    }
    var api = {
      select: function (row) { sel = row; draw(); return api; },
      clear: function () { sel = null; draw(); return api; },
      get: function () { return sel; },
      redraw: draw
    };
    $el.off('click.jdops').on('click.jdops', '.jd-ops__btn', function () {
      var a = actions[+$(this).attr('data-i')];
      if (!a || a.sep || (a.needRow && !sel)) return;
      if (a.onClick) a.onClick(sel, api);
    });
    draw();
    return api;
  }

  /* ---------------------------------------------------- 表格 */
  function table(cfg) {
    var $el = $(cfg.el);
    var st = { page: 1, size: cfg.pageSize || 20, params: $.extend({}, cfg.params || {}), total: 0 };
    var columns = cfg.columns || [];
    var actions = cfg.ops || [];
    var sel = null, lastRows = [];

    function rowKey(row) { return row[cfg.idField || 'id']; }

    function refreshOps() {
      if (!actions.length) return;
      $el.find('.jd-ops').html(opsInner(actions, sel, cfg.hint));
      bindPerms($el);
    }

    function colHtml(c, row) {
      var v = row[c.field];
      var content = c.render ? c.render(v, row) : esc(v === null || v === undefined || v === '' ? '-' : v);
      return '<td class="' + (c.className || '') + '"' + (c.width ? ' style="width:' + c.width + 'px"' : '') + '>' + content + '</td>';
    }

    function render(d) {
      st.total = d.total || 0;
      lastRows = d.rows || [];
      var head = columns.map(function (c) {
        return '<th' + (c.width ? ' style="width:' + c.width + 'px"' : '') + '>' + (c.title || '') + '</th>';
      }).join('');
      var body = (d.rows || []).map(function (row) {
        return '<tr data-id="' + esc(rowKey(row)) + '">' +
          columns.map(function (c) { return colHtml(c, row); }).join('') + '</tr>';
      }).join('');
      var empty = '<tr><td colspan="' + columns.length + '"><div class="jd-empty">' +
        icon('folder') + '暂无数据</div></td></tr>';
      var opsHtml = actions.length ? '<div class="jd-ops">' + opsInner(actions, sel, cfg.hint) + '</div>' : '';
      var html = opsHtml + '<div class="jd-table-wrap"><table class="jd-table"><thead><tr>' + head +
        '</tr></thead><tbody>' + (body || empty) + '</tbody></table></div>' + pager(d);
      $el.html(html);
      // 选中态还原
      if (sel) {
        var keep = (d.rows || []).filter(function (r) { return rowKey(r) === rowKey(sel); })[0];
        sel = keep || null;
        if (keep) $el.find('tr[data-id="' + esc(rowKey(keep)) + '"]').addClass('is-selected');
        refreshOps();
      }
      if (cfg.onLoaded) cfg.onLoaded(d);
      bindPerms($el);
      $el.off('click', '.jd-pager button').on('click', '.jd-pager button', function () {
        var p = $(this).data('page');
        if (p && p !== st.page) { st.page = p; load(); }
      });
    }

    function pager(d) {
      var pages = d.pages || 1, cur = d.page || 1;
      var arr = [], start = Math.max(1, cur - 2), end = Math.min(pages, start + 4);
      start = Math.max(1, end - 4);
      for (var i = start; i <= end; i++) arr.push(i);
      return '<div class="jd-pager"><span class="total">共 ' + fmtNum(d.total) + ' 条 · 第 ' +
        cur + '/' + pages + ' 页</span>' +
        '<button ' + (cur <= 1 ? 'disabled' : '') + ' data-page="' + (cur - 1) + '">上一页</button>' +
        arr.map(function (p) {
          return '<button class="' + (p === cur ? 'is-active' : '') + '" data-page="' + p + '">' + p + '</button>';
        }).join('') +
        '<button ' + (cur >= pages ? 'disabled' : '') + ' data-page="' + (cur + 1) + '">下一页</button></div>';
    }

    function load() {
      var params = $.extend({ page: st.page, size: st.size }, st.params);
      Object.keys(params).forEach(function (k) {
        if (params[k] === '' || params[k] === null || params[k] === undefined) delete params[k];
      });
      return get(cfg.url, params).then(render);
    }

    // 行选中 / 行级操作
    $el.on('click', 'tbody tr', function () {
      var id = $(this).attr('data-id');
      if (!id) return;
      var row = lastRows.filter(function (r) { return String(rowKey(r)) === String(id); })[0];
      if (!row) return;
      if (sel && String(rowKey(sel)) === String(id)) {
        sel = null; $(this).removeClass('is-selected'); refreshOps(); return;
      }
      sel = row;
      $el.find('tbody tr').removeClass('is-selected');
      $(this).addClass('is-selected');
      refreshOps();
    });
    $el.on('click', '.jd-ops__btn', function () {
      var a = actions[+$(this).attr('data-i')];
      if (!a || a.sep || (a.needRow && !sel)) return;
      if (a.onClick) a.onClick(sel);
    });
    $el.on('dblclick', 'tbody tr', function () {
      var a = actions.filter(function (x) { return x.needRow && !x.sep; })[0];
      if (a && sel && a.onClick) a.onClick(sel);
    });

    load();
    return {
      reload: function (resetPage) { if (resetPage) st.page = 1; return load(); },
      setParams: function (p) { $.extend(st.params, p); st.page = 1; return load(); },
      getParams: function () { return st.params; },
      getSelected: function () { return sel; },
      clearSelected: function () { sel = null; $el.find('tbody tr').removeClass('is-selected'); refreshOps(); },
      state: st
    };
  }

  /* ---------------------------------------------------- 树 */
  function renderTree($el, nodes, opts) {
    opts = opts || {};
    var checked = {};
    (opts.checked || []).forEach(function (id) { checked[id] = true; });
    var onlyLeaf = !!opts.leafOnly;

    function childIds(n) {
      var out = [];
      (function walk(x) { (x.children || []).forEach(function (c) { out.push(c.id); walk(c); }); })(n);
      return out;
    }

    function nodeHtml(n) {
      var kids = n.children || [];
      var isLeaf = kids.length === 0;
      var showCheck = opts.checkable && (!onlyLeaf || isLeaf);
      var html = '<div class="jd-tree__node" data-id="' + esc(n.id) + '">' +
        '<div class="jd-tree__row">' +
        '<span class="jd-tree__caret ' + (isLeaf ? 'is-leaf' : '') + '">' +
        icon('chevron-right') + '</span>' +
        (showCheck ? '<input type="checkbox" class="jd-tree__ck" ' +
          (checked[n.id] ? 'checked' : '') + '>' : '') +
        '<span class="jd-tree__label">' + esc(n.menu_name || n.name || n.label) + '</span>' +
        (n.perm_code ? '<span class="jd-tree__badge">' + esc(n.perm_code) + '</span>' : '') +
        '</div>';
      if (!isLeaf) {
        html += '<div class="jd-tree__children">' + kids.map(nodeHtml).join('') + '</div>';
      }
      return html + '</div>';
    }

    $el.html('<div class="jd-tree">' + nodes.map(nodeHtml).join('') + '</div>');
    if (opts.expandAll !== false) $el.find('.jd-tree__children').addClass('is-open');

    $el.off('click', '.jd-tree__caret').on('click', '.jd-tree__caret', function (e) {
      e.stopPropagation();
      var $c = $(this).closest('.jd-tree__node').children('.jd-tree__children');
      $c.toggleClass('is-open');
      $(this).toggleClass('is-open');
    });
    $el.off('click', '.jd-tree__row').on('click', '.jd-tree__row', function (e) {
      if ($(e.target).is('input')) return;
      var $ck = $(this).find('.jd-tree__ck');
      if ($ck.length) { $ck.prop('checked', !$ck.prop('checked')).trigger('change'); }
    });
    $el.off('change', '.jd-tree__ck').on('change', '.jd-tree__ck', function () {
      var on = this.checked;
      var $node = $(this).closest('.jd-tree__node');
      $node.find('.jd-tree__ck').each(function () { this.checked = on; });
      var $parent = $node.parent().closest('.jd-tree__node');
      while ($parent.length) {
        var all = $parent.find('> .jd-tree__row .jd-tree__ck');
        var kids = $parent.find('> .jd-tree__children > .jd-tree__node > .jd-tree__row .jd-tree__ck');
        all.prop('checked', kids.length > 0 && kids.filter(':checked').length === kids.length);
        $parent = $parent.parent().closest('.jd-tree__node');
      }
    });

    return {
      getChecked: function () {
        return $el.find('.jd-tree__ck:checked').map(function () {
          return $(this).closest('.jd-tree__node').data('id');
        }).get();
      },
      setChecked: function (ids) {
        $el.find('.jd-tree__ck').prop('checked', false);
        (ids || []).forEach(function (id) {
          $el.find('.jd-tree__node[data-id="' + id + '"] > .jd-tree__row .jd-tree__ck').prop('checked', true);
        });
      },
      checkAll: function (on) { $el.find('.jd-tree__ck').prop('checked', !!on); }
    };
  }

  function treeDialog(cfg) {
    var body = '<div class="jd-flex jd-mb8">' +
      '<button class="jd-btn jd-btn--sm" data-act="all">全选</button>' +
      '<button class="jd-btn jd-btn--sm" data-act="none">全不选</button>' +
      '<button class="jd-btn jd-btn--sm" data-act="expand">展开/收起</button>' +
      '<span class="jd-grow"></span><span class="jd-hint">' + (cfg.hint || '勾选后保存，父节点会自动补齐') + '</span></div>' +
      '<div id="jdTreeBox" style="max-height:52vh;overflow:auto;border:1px solid var(--border-light);border-radius:8px;padding:8px"></div>';
    return new Promise(function (resolve) {
      dialog({
        title: cfg.title, width: 'jd-dialog--lg', body: body,
        footer: '<button class="jd-btn" data-act="cancel">取消</button>' +
          '<button class="jd-btn jd-btn--primary" data-act="save">保存</button>',
        onOpen: function ($m, close) {
          var api = renderTree($m.find('#jdTreeBox'), cfg.nodes || [], cfg);
          $m.on('click', '[data-act="all"]', function () { api.checkAll(true); });
          $m.on('click', '[data-act="none"]', function () { api.checkAll(false); });
          $m.on('click', '[data-act="expand"]', function () {
            $m.find('.jd-tree__children').toggleClass('is-open');
          });
          $m.on('click', '[data-act="cancel"]', function () { close(); resolve(null); });
          $m.on('click', '[data-act="save"]', function () {
            var ids = api.getChecked();
            close();
            resolve(ids);
          });
        },
        onClose: function () { resolve(null); }
      });
    });
  }

  /* ---------------------------------------------------- 水印 */
  function watermark(text) {
    var $w = $('.jd-watermark').empty();
    if (!text) return;
    var w = window.innerWidth, h = window.innerHeight;
    var stepX = 260, stepY = 150;
    var html = '';
    for (var y = 0; y < h + stepY; y += stepY) {
      for (var x = -60; x < w + stepX; x += stepX) {
        html += '<span style="left:' + x + 'px;top:' + y + 'px">' + esc(text) + '</span>';
      }
    }
    $w.html(html);
  }

  /* ---------------------------------------------------- 通用小工具 */
  function qs(name) {
    var m = new RegExp('[?&]' + name + '=([^&]*)').exec(location.search);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function badge(text, type) {
    return '<span class="jd-tag jd-tag--' + (type || 'info') + '">' + esc(text) + '</span>';
  }

  /* 图片预览（聊天截图/图片点击放大） */
  function previewImage(url, name) {
    var body = '<div style="text-align:center"><img src="' + esc(url) + '" alt="" ' +
      'style="max-width:100%;max-height:70vh;border-radius:8px"></div>' +
      (name ? '<div class="jd-hint" style="text-align:center;margin-top:8px">' + esc(name) + '</div>' : '');
    return dialog({
      title: '图片预览', width: 'jd-dialog--lg', body: body,
      footer: '<button class="jd-btn" data-act="close">关闭</button>' +
        '<a class="jd-btn jd-btn--primary" href="' + esc(url) + '" download="' + esc(name || 'image') + '">下载</a>',
      onOpen: function ($m, close) { $m.on('click', '[data-act="close"]', close); }
    });
  }

  function qualityTag(score, status) {
    var t = status === 'PASS' ? 'success' : (status === 'REJECT' ? 'danger' : 'warning');
    var txt = status === 'PASS' ? '通过' : (status === 'REJECT' ? '未通过' : '待检');
    return '<span class="jd-tag jd-tag--' + t + '">' + txt + ' ' + (score === null || score === undefined ? '-' : Number(score).toFixed(2)) + '</span>';
  }

  return {
    state: state, icon: icon, req: req, get: get, post: post, upload: upload,
    dialog: dialog, alert: alert_, confirm: confirm_, formDialog: formDialog,
    loading: loading, hasPerm: hasPerm, bindPerms: bindPerms,
    THEMES: THEMES, applyTheme: applyTheme, initTheme: initTheme,
    loadDicts: loadDicts, dict: dict, label: label,
    table: table, opsBar: opsBar, renderTree: renderTree, treeDialog: treeDialog,
    fmtSize: fmtSize, fmtNum: fmtNum, fmtTime: fmtTime, esc: esc,
    watermark: watermark, qs: qs, tag: badge, qualityTag: qualityTag, previewImage: previewImage,
    init: function (cfg) {
      state.boot = cfg.boot || {};
      state.user = cfg.user || {};
      state.perms = {};
      (cfg.perms || []).forEach(function (p) { state.perms[p] = true; });
      state.theme = state.user.pref_theme || 'blue';
      initTheme();
      return loadDicts();
    }
  };
})(jQuery);
