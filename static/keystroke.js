
class KeystrokeCapture {
  constructor(options = {}) {
    this.reset();
    this.onUpdate = options.onUpdate || null;  
    this.minKeys  = options.minKeys  || 4;    
  }

  reset() {
    this._keyDownTimes  = {};   
    this._lastKeyUpTime  = null;
    this._lastKeyDownTime = null;
    this.dwellTimes     = [];   
    this.flightTimes    = [];  
    this.pressIntervals = [];  
    this.backspaceCount = 0;
    this.totalKeys      = 0;
    this.startTime      = null;
    this.endTime        = null;
  }

  attach(el) {
    this._el = el;
    this._handleDown = (e) => this.onKeyDown(e);
    this._handleUp   = (e) => this.onKeyUp(e);
    el.addEventListener('keydown', this._handleDown);
    el.addEventListener('keyup',   this._handleUp);
    return this;
  }

  detach() {
    if (this._el) {
      this._el.removeEventListener('keydown', this._handleDown);
      this._el.removeEventListener('keyup',   this._handleUp);
    }
  }

  onKeyDown(e) {
    const now = performance.now();
    if (!this.startTime) this.startTime = now;

    this._keyDownTimes[e.code] = now;

    if (this._lastKeyDownTime !== null) {
      this.pressIntervals.push(now - this._lastKeyDownTime);
    }
    this._lastKeyDownTime = now;

    if (this._lastKeyUpTime !== null) {
      const flight = now - this._lastKeyUpTime;
      if (flight > 0 && flight < 2000) {  
        this.flightTimes.push(flight);
      }
    }

    if (e.key === 'Backspace') this.backspaceCount++;
    this.totalKeys++;

    if (this.onUpdate) this.onUpdate(this.getStats());
  }

  onKeyUp(e) {
    const now = performance.now();
    this.endTime = now;

    if (this._keyDownTimes[e.code] !== undefined) {
      const dwell = now - this._keyDownTimes[e.code];
      if (dwell > 0 && dwell < 1000) {     
        this.dwellTimes.push(dwell);
      }
      delete this._keyDownTimes[e.code];
    }
    this._lastKeyUpTime = now;
  }


  getData() {
    return {
      dwell_times:    [...this.dwellTimes],
      flight_times:   [...this.flightTimes],
      press_intervals:[...this.pressIntervals],
      backspace_count: this.backspaceCount,
      total_keys:      this.totalKeys,
      duration_ms:     this.endTime && this.startTime
                         ? (this.endTime - this.startTime)
                         : 1
    };
  }


  getStats() {
    const mean = arr => arr.length ? arr.reduce((a,b)=>a+b,0)/arr.length : 0;
    const std  = arr => {
      if (arr.length < 2) return 0;
      const m = mean(arr);
      return Math.sqrt(arr.reduce((a,b)=>a+(b-m)**2,0)/arr.length);
    };

    const duration = (this.endTime||performance.now()) - (this.startTime||performance.now());
    return {
      avg_dwell:    Math.round(mean(this.dwellTimes)),
      avg_flight:   Math.round(mean(this.flightTimes)),
      avg_interval: Math.round(mean(this.pressIntervals)),
      backspace_rate: this.totalKeys > 0
        ? (this.backspaceCount / this.totalKeys).toFixed(3)
        : '0.000',
      typing_speed: duration > 0
        ? ((this.totalKeys / duration) * 1000).toFixed(2)
        : '0.00',
      jitter:       Math.round(std(this.flightTimes)),
      samples:      this.dwellTimes.length
    };
  }

  isReady() {
    return this.totalKeys >= this.minKeys;
  }
}



class EnrollmentCollector {
  constructor(targetSamples = 10) {
    this.targetSamples = targetSamples;
    this.samples = [];
    this.capture = null;
  }

  init(inputEl, onProgress) {
    this.capture = new KeystrokeCapture({ minKeys: 6 });
    this.capture.attach(inputEl);
    this._onProgress = onProgress || (() => {});
    return this;
  }

  recordSample() {
    if (!this.capture || !this.capture.isReady()) return false;
    this.samples.push(this.capture.getData());
    this.capture.reset();
    this._onProgress(this.samples.length, this.targetSamples);
    return true;
  }

  isComplete() {
    return this.samples.length >= this.targetSamples;
  }

  getSamples() {
    return [...this.samples];
  }

  reset() {
    this.samples = [];
    if (this.capture) this.capture.reset();
  }
}



class BiometricHUD {
  /**
   * Renders a live stats panel.
   * @param {HTMLElement} container – where to render
   * @param {KeystrokeCapture} capture – capture instance to poll
   */
  constructor(container, capture) {
    this.container = container;
    this.capture   = capture;
    this._render();
    capture.onUpdate = () => this._render();
  }

  _render() {
    const s = this.capture.getStats();
    this.container.innerHTML = `
      <div class="hud-grid">
        <div class="hud-item">
          <span class="hud-label">Dwell</span>
          <span class="hud-value">${s.avg_dwell}<span class="hud-unit">ms</span></span>
        </div>
        <div class="hud-item">
          <span class="hud-label">Flight</span>
          <span class="hud-value">${s.avg_flight}<span class="hud-unit">ms</span></span>
        </div>
        <div class="hud-item">
          <span class="hud-label">Speed</span>
          <span class="hud-value">${s.typing_speed}<span class="hud-unit">k/s</span></span>
        </div>
        <div class="hud-item">
          <span class="hud-label">Jitter</span>
          <span class="hud-value">${s.jitter}<span class="hud-unit">ms</span></span>
        </div>
        <div class="hud-item">
          <span class="hud-label">Backspace</span>
          <span class="hud-value">${(parseFloat(s.backspace_rate)*100).toFixed(1)}<span class="hud-unit">%</span></span>
        </div>
        <div class="hud-item">
          <span class="hud-label">Samples</span>
          <span class="hud-value">${s.samples}</span>
        </div>
      </div>`;
  }
}



const BioShieldAPI = {
  baseUrl: '',

  getToken() {
    return localStorage.getItem('bs_token');
  },

  setToken(t) {
    localStorage.setItem('bs_token', t);
  },

  clearToken() {
    localStorage.removeItem('bs_token');
    localStorage.removeItem('bs_user');
  },

  getUser() {
    try { return JSON.parse(localStorage.getItem('bs_user') || 'null'); }
    catch { return null; }
  },

  setUser(u) {
    localStorage.setItem('bs_user', JSON.stringify(u));
  },

  async request(path, method = 'GET', body = null) {
    const headers = { 'Content-Type': 'application/json' };
    const token = this.getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const opts = { method, headers };
    if (body) opts.body = JSON.stringify(body);

    const res = await fetch(this.baseUrl + path, opts);
    const data = await res.json();
    if (!res.ok) throw { status: res.status, ...data };
    return data;
  },

  signup(payload)      { return this.request('/api/signup',     'POST', payload); },
  login(payload)       { return this.request('/api/login',      'POST', payload); },
  enroll(payload)      { return this.request('/api/enroll',     'POST', payload); },
  test(payload)        { return this.request('/api/test',       'POST', payload); },
  payment(payload)     { return this.request('/api/payment',    'POST', payload); },
  otpVerify(payload)   { return this.request('/api/otp-verify', 'POST', payload); },
  faceVerify(payload)  { return this.request('/api/face-verify','POST', payload); },
  riskHistory()        { return this.request('/api/risk-history'); },
};



function renderRiskBadge(decision, score) {
  const config = {
    ALLOW:        { color: '#00ff88', label: '✓ ALLOW',        bg: '#00ff8820' },
    OTP_REQUIRED: { color: '#ffcc00', label: '⚠ OTP REQUIRED', bg: '#ffcc0020' },
    BLOCK:        { color: '#ff4466', label: '✕ BLOCKED',      bg: '#ff446620' },
  };
  const c = config[decision] || config.ALLOW;
  const pct = Math.round((score || 0) * 100);
  return `
    <div style="
      border:1px solid ${c.color};
      background:${c.bg};
      color:${c.color};
      padding:12px 20px;
      border-radius:8px;
      font-family:'Share Tech Mono',monospace;
      display:inline-flex;
      align-items:center;
      gap:12px;
    ">
      <span style="font-size:1.1em;font-weight:700">${c.label}</span>
      <span style="opacity:0.7;font-size:0.85em">Risk: ${pct}%</span>
    </div>`;
}

window.KeystrokeCapture    = KeystrokeCapture;
window.EnrollmentCollector = EnrollmentCollector;
window.BiometricHUD        = BiometricHUD;
window.BioShieldAPI        = BioShieldAPI;
window.renderRiskBadge     = renderRiskBadge;
