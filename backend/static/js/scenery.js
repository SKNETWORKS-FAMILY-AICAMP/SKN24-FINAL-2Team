/* PoliCity — decorative scenery: drifting clouds + gentle parallax.
   Header/footer/content are real HTML in each page; this only animates ambiance. */
(function () {
  function buildClouds() {
    var sky = document.querySelector('.sky-clouds');
    if (!sky) return;
    var rows = [
      { top: 8,  scale: 1.0,  dur: 70, delay: 0 },
      { top: 18, scale: 0.7,  dur: 92, delay: -34 },
      { top: 13, scale: 1.25, dur: 58, delay: -48 },
      { top: 30, scale: 0.85, dur: 100,delay: -16 },
      { top: 24, scale: 1.05, dur: 80, delay: -62 },
      { top: 40, scale: 0.65, dur: 112,delay: -22 }
    ];
    rows.forEach(function (r) {
      var c = document.createElement('img');
      c.src = '/static/assets/cloud.png';
      c.alt = '';
      c.className = 'cloud';
      c.style.top = r.top + '%';
      c.style.width = (88 * r.scale) + 'px';
      c.style.animation = 'drift ' + r.dur + 's linear ' + r.delay + 's infinite';
      if (Math.random() > 0.5) c.style.transform = 'scaleX(-1)';
      sky.appendChild(c);
    });
  }

  function parallax() {
    var flora = document.querySelectorAll('.flora');
    var lamps = document.querySelectorAll('.lamp');
    var ticking = false;
    window.addEventListener('scroll', function () {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        var y = window.scrollY * 0.06;
        flora.forEach(function (f) { f.style.transform = 'translateY(' + y + 'px)'; });
        lamps.forEach(function (l) { l.style.transform = 'translateY(' + (y * 0.7) + 'px)'; });
        ticking = false;
      });
    }, { passive: true });
  }

// 헤더가 2개(비로그인/로그인) 동시에 존재해서 querySelector(단수) 대신 querySelectorAll로 전체 버튼에 이벤트를 걸고,
// 클릭된 버튼이 속한 헤더의 main-nav만 토글하도록 수정. 외부 클릭 시 닫기 로직도 추가.
  function mobileNav() {
    var MOBILE_BP = 1300;

    document.querySelectorAll('.nav-toggle').forEach(function (btn) {
      var header = btn.closest('.site-header');
      var nav = header.querySelector('.main-nav');
      var navInner = header.querySelector('.nav-inner');
      var actions = header.querySelector('.header-actions');

      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        nav.classList.toggle('open');
      });

      if (!actions) return;

      var placeholder = document.createComment('header-actions-anchor');
      actions.parentElement.insertBefore(placeholder, actions);

      function syncActionsPosition() {
        var isMobile = window.innerWidth <= MOBILE_BP;
        if (isMobile && actions.parentElement !== nav) {
          nav.appendChild(actions);
        } else if (!isMobile && actions.parentElement !== navInner) {
          placeholder.parentElement.insertBefore(actions, placeholder.nextSibling);
        }
      }

      syncActionsPosition();
      window.addEventListener('resize', syncActionsPosition);
    });

    document.addEventListener('click', function (e) {
      if (!e.target.closest('.site-header')) {
        document.querySelectorAll('.main-nav.open').forEach(function (nav) {
          nav.classList.remove('open');
        });
      }
    });
  }

  // 헤더의 실제 렌더링 높이를 CSS 변수로 흘려보내서,
  // 정보카드 드롭다운이 폰트/로고 크기 등과 무관하게 항상 헤더 밑선에 정확히 붙도록 함
  function measureHeader(header) {
    if (getComputedStyle(header).display === 'none') return;
    var headerBox = header.getBoundingClientRect();
    header.style.setProperty('--header-h', headerBox.height + 'px');

    header.querySelectorAll('.nav-dropdown').forEach(function (dd) {
      var ddBox = dd.getBoundingClientRect();
      dd.style.setProperty('--dropdown-x', (ddBox.left - headerBox.left) + 'px');
    });
  }

  function syncHeaderHeight() {
    var headers = document.querySelectorAll('.site-header');
    function update() {
      headers.forEach(measureHeader);
    }
    update();
    window.addEventListener('resize', update);
    // 폰트 로딩 등으로 높이가 늦게 확정되는 경우 대비
    window.addEventListener('load', update);
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(update);
    }
  }

  window.PoliCitySyncHeaderHeight = syncHeaderHeight;

  function navDropdown() {
    document.querySelectorAll('.nav-dropdown').forEach(function (dd) {
      var trigger = dd.querySelector('.nav-link');
      var header = dd.closest('.site-header');
      // toggle on click (so it works on touch / and lets parent link not navigate to '#')
      trigger.addEventListener('click', function (e) {
        e.preventDefault();
        // 열기 직전에 즉시 재측정 → 레이아웃이 100% 확정된 시점이라 항상 정확함
        if (header) measureHeader(header);
        dd.classList.toggle('open');
      });
    });
    document.addEventListener('click', function (e) {
      document.querySelectorAll('.nav-dropdown.open').forEach(function (dd) {
        if (!dd.contains(e.target)) dd.classList.remove('open');
      });
    });
  }

  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }
  ready(function () { buildClouds(); parallax(); mobileNav(); navDropdown(); syncHeaderHeight(); });
})();