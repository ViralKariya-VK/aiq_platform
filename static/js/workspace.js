// ── Monaco ───────────────────────────────────────────────────────────────────
let editor;

require.config({
    paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs' }
});

require(['vs/editor/editor.main'], function () {
    editor = monaco.editor.create(document.getElementById('monacoEditor'), {
        value: '# Start coding here\n',
        language: 'python',
        theme: 'vs-dark',
        fontSize: 14,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        automaticLayout: true,
        lineNumbers: 'on',
        padding: { top: 16 },
        fontFamily: "'Fira Code', 'Courier New', monospace",
        fontLigatures: true,
    });
});


// ── Marked + Highlight ───────────────────────────────────────────────────────
const renderer = new marked.Renderer();

renderer.code = function (code, lang) {
    const language = lang || 'plaintext';
    let highlighted;
    try {
        highlighted = hljs.getLanguage(language)
            ? hljs.highlight(code, { language }).value
            : hljs.highlightAuto(code).value;
    } catch {
        highlighted = code;
    }
    return `<div class="code-block-wrapper">
        <div class="code-block-header">
            <span class="code-lang">${language}</span>
            <button class="copy-btn" onclick="copyCode(this)">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="9" y="9" width="13" height="13" rx="2"/>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                </svg>
                Copy
            </button>
        </div>
        <pre><code class="hljs language-${language}">${highlighted}</code></pre>
    </div>`;
};

marked.setOptions({ renderer, breaks: true, gfm: true });


// ── Copy code ────────────────────────────────────────────────────────────────
function copyCode(btn) {
    const code = btn.closest('.code-block-wrapper').querySelector('code').innerText;
    navigator.clipboard.writeText(code).then(() => {
        btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> Copied!`;
        btn.classList.add('copied');
        setTimeout(() => {
            btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> Copy`;
            btn.classList.remove('copied');
        }, 2000);
    });
}


// ── Render existing markdown on load ─────────────────────────────────────────
const messages = document.getElementById('messages');

document.querySelectorAll('.markdown-render').forEach(el => {
    el.innerHTML = marked.parse(el.textContent || '');
});

function scrollBottom() {
    messages.scrollTop = messages.scrollHeight;
}

scrollBottom();


// ── Toggle ───────────────────────────────────────────────────────────────────
const toggleBtn = document.getElementById('toggleAI');
const aiPanel = document.getElementById('aiPanel');
const editorPanel = document.getElementById('editorPanel');

toggleBtn.addEventListener('click', function () {
    const isHidden = aiPanel.classList.contains('hidden');
    if (isHidden) {
        aiPanel.classList.remove('hidden');
        editorPanel.classList.remove('full-width');
        toggleBtn.textContent = 'Hide AI';
    } else {
        aiPanel.classList.add('hidden');
        editorPanel.classList.add('full-width');
        toggleBtn.textContent = 'Show AI';
    }
    setTimeout(() => { if (editor) editor.layout(); }, 50);
});


// ── Auto-grow textarea ───────────────────────────────────────────────────────
const promptInput = document.getElementById('promptInput');

promptInput.addEventListener('input', () => {
    promptInput.style.height = 'auto';
    promptInput.style.height = Math.min(promptInput.scrollHeight, 130) + 'px';
});

promptInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendPrompt();
    }
});


// ── Send Prompt ──────────────────────────────────────────────────────────────
const sendBtn = document.getElementById('sendBtn');
sendBtn.addEventListener('click', sendPrompt);

async function sendPrompt() {
    const text = promptInput.value.trim();
    if (!text) return;

    const code = editor ? editor.getValue() : '';

    sendBtn.disabled = true;
    promptInput.disabled = true;

    const emptyChat = document.querySelector('.empty-chat');
    if (emptyChat) emptyChat.remove();

    const now = new Date();
    const time = now.getHours().toString().padStart(2, '0') + ':' + now.getMinutes().toString().padStart(2, '0');

    const userRow = document.createElement('div');
    userRow.className = 'msg-row msg-user';
    userRow.innerHTML = `
        <div class="msg-bubble msg-user-bubble">
            <div class="msg-text">${escapeHTML(text)}</div>
            <div class="msg-time">${time}</div>
        </div>`;
    messages.appendChild(userRow);

    promptInput.value = '';
    promptInput.style.height = 'auto';
    scrollBottom();

    const typingRow = document.createElement('div');
    typingRow.className = 'msg-row msg-ai';
    typingRow.innerHTML = `
        <div class="msg-bubble msg-ai-bubble">
            <div class="typing-dots">
                <span></span><span></span><span></span>
            </div>
        </div>`;
    messages.appendChild(typingRow);
    scrollBottom();

    try {
        const res = await fetch(SEND_PROMPT_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': CSRF_TOKEN
            },
            body: JSON.stringify({ prompt_text: text, current_code: code })
        });

        const data = await res.json();
        typingRow.remove();

        const aiRow = document.createElement('div');
        aiRow.className = 'msg-row msg-ai';
        const bubble = document.createElement('div');
        bubble.className = 'msg-bubble msg-ai-bubble';
        bubble.innerHTML = marked.parse(data.response);
        aiRow.appendChild(bubble);
        messages.appendChild(aiRow);

    } catch {
        typingRow.remove();
        const errRow = document.createElement('div');
        errRow.className = 'msg-row msg-ai';
        errRow.innerHTML = `<div class="msg-bubble msg-ai-bubble" style="color:#f44336">Something went wrong. Please try again.</div>`;
        messages.appendChild(errRow);
    }

    sendBtn.disabled = false;
    promptInput.disabled = false;
    promptInput.focus();
    scrollBottom();
}


// ── Helpers ──────────────────────────────────────────────────────────────────
function escapeHTML(t) {
    return t
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// ── Pyodide Setup ─────────────────────────────────────────────────────────────
let pyodide = null;
let pyodideReady = false;

const runBtn = document.getElementById('runBtn');
const outputContent = document.getElementById('outputContent');

runBtn.textContent = '▶ Loading Python...';
runBtn.disabled = true;

async function loadPyodideEnv() {
    try {
        pyodide = await loadPyodide();

        // Load scientific packages
        await pyodide.loadPackage(['numpy', 'pandas', 'matplotlib']);

        // Redirect stdout and stderr to our output panel
        pyodide.runPython(`
import sys
import io

class OutputCapture:
    def __init__(self):
        self.data = []
    def write(self, s):
        self.data.append(s)
    def flush(self):
        pass
    def getvalue(self):
        return ''.join(self.data)

sys.stdout = OutputCapture()
sys.stderr = OutputCapture()
        `);

        pyodideReady = true;
        runBtn.textContent = '▶ Run';
        runBtn.disabled = false;

    } catch (err) {
        outputContent.innerHTML = `<span class="output-error">Failed to load Python environment: ${err}</span>`;
        runBtn.textContent = '▶ Run';
        runBtn.disabled = false;
    }
}

loadPyodideEnv();


// ── Run Code ─────────────────────────────────────────────────────────────────
runBtn.addEventListener('click', async function () {
    if (!pyodideReady) {
        outputContent.innerHTML = `<span class="output-loading">Python is still loading, please wait...</span>`;
        return;
    }

    const code = editor ? editor.getValue() : '';
    if (!code.trim()) {
        outputContent.innerHTML = `<span class="output-placeholder">Nothing to run.</span>`;
        return;
    }

    runBtn.disabled = true;
    runBtn.textContent = '▶ Running...';
    outputContent.innerHTML = `<span class="output-loading">Running...</span>`;

    // Small delay to let UI update
    await new Promise(r => setTimeout(r, 50));

    try {
        // Reset output capture
        pyodide.runPython(`
sys.stdout.data = []
sys.stderr.data = []
        `);

        // Run student code
        await pyodide.runPythonAsync(code);

        const stdout = pyodide.runPython(`sys.stdout.getvalue()`);
        const stderr = pyodide.runPython(`sys.stderr.getvalue()`);

        let output = '';
        if (stdout) output += stdout;
        if (stderr) output += stderr;

        if (output.trim()) {
            outputContent.innerHTML = `<span class="output-success">${escapeHTML(output)}</span>`;
        } else {
            outputContent.innerHTML = `<span class="output-placeholder">Code ran successfully with no output.</span>`;
        }

    } catch (err) {
        outputContent.innerHTML = `<span class="output-error">${escapeHTML(String(err))}</span>`;
    }

    runBtn.disabled = false;
    runBtn.textContent = '▶ Run';
});


// ── Clear Output ─────────────────────────────────────────────────────────────
document.getElementById('clearOutput').addEventListener('click', () => {
    outputContent.innerHTML = `<span class="output-placeholder">Run your code to see output here...</span>`;
});


// ── Submit ────────────────────────────────────────────────────────────────────
const submitBtn = document.getElementById('submitBtn');
const popupOverlay = document.getElementById('popupOverlay');
const popupClose = document.getElementById('popupClose');

submitBtn.addEventListener('click', () => {
    popupOverlay.classList.add('visible');
});

popupClose.addEventListener('click', () => {
    popupOverlay.classList.remove('visible');
});

popupOverlay.addEventListener('click', (e) => {
    if (e.target === popupOverlay) {
        popupOverlay.classList.remove('visible');
    }
});