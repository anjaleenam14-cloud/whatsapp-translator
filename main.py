import time
import os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from deep_translator import GoogleTranslator
import threading
import asyncio
import base64
import edge_tts

# --- CONFIGURATION ---
TARGET_LANGUAGES = {
    "English": "en",
    "Hindi": "hi",
    "Marathi": "mr",
    "Tamil": "ta",
    "Telugu": "te"
}
DEFAULT_TARGET = "Hindi"

# WhatsApp Selectors (as of 2026-era known data-testids)
MSG_CONTAINER_SELECTOR = "[data-testid='msg-container']"
MSG_TEXT_SELECTOR = ".copyable-text span"

# --- INJECTED JAVASCRIPT ---
INJECTED_SCRIPT = """
(function() {
    if (window.whatsappTranslatorLoaded && document.getElementById('wa-translator-ui')) return;
    window.whatsappTranslatorLoaded = true;

    // TTS Locale Mapping
    const langLocales = {
        'en': 'en-US',
        'hi': 'hi-IN',
        'mr': 'mr-IN',
        'ta': 'ta-IN',
        'te': 'te-IN'
    };

    // Create UI Overlay
    const ui = document.createElement('div');
    ui.id = 'wa-translator-ui';
    ui.style.cssText = `
        position: fixed;
        bottom: 30px;
        right: 30px;
        width: 380px;
        background: white !important;
        border: 2px solid #25d366;
        border-radius: 20px;
        box-shadow: 0 15px 45px rgba(0,0,0,0.3);
        z-index: 10000;
        font-family: 'Segoe UI', Helvetica, Arial, sans-serif;
        padding: 20px;
        display: flex;
        flex-direction: column;
        gap: 12px;
        color: #111 !important;
    `;

    // Fallback if waTargetLangs is missing
    const langs = window.waTargetLangs || {"English": "en", "Hindi": "hi", "Marathi": "mr", "Tamil": "ta", "Telugu": "te"};

    ui.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 5px;">
            <div style="display:flex; align-items:center; gap:8px;">
                <div id="wa-status-dot" style="width:12px; height:12px; background:#25d366; border-radius:50%; box-shadow: 0 0 8px #25d366;"></div>
                <strong style="color: #075e54; font-size: 16px;">AI Assistant</strong>
            </div>
            <select id="wa-target-lang" style="padding: 5px 12px; border-radius: 8px; border: 1px solid #ccc; background: white !important; color: black !important; font-size: 14px; cursor: pointer; outline: none; display: block !important;">
                ${Object.entries(langs).map(([name, code]) => `<option value="${code}" ${name === 'Hindi' ? 'selected' : ''} style="color: black !important; background: white !important;">${name}</option>`).join('')}
            </select>
        </div>
        <div id="wa-trans-display" style="background: #f0f2f5; border-radius: 12px; padding: 15px; border: 1px solid #ddd; min-height: 80px; display: flex; flex-direction: column; justify-content: center;">
            <div id="wa-trans-content" style="font-size: 15px; line-height: 1.5; color: #111 !important;">
                <span style="color: #666;">1. Select a chat on the left.<br>2. Click any received message.</span>
            </div>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:center; opacity: 0.8; font-size: 12px; color: #555;">
            <span id="wa-action-status">Ready</span>
            <span>v1.7 Premium</span>
        </div>
    `;

    document.body.appendChild(ui);

    // Speak Function - Optimized for Correct Language Selection
    window.speakMessage = function(text, langCode) {
        if (!window.speechSynthesis) return;
        window.speechSynthesis.cancel();
        
        const utterance = new SpeechSynthesisUtterance(text);
        const locale = langLocales[langCode] || 'en-US';
        utterance.lang = locale;
        
        // Wait for voices to load if needed
        let voices = window.speechSynthesis.getVoices();
        
        // Find a matching voice
        let voice = voices.find(v => v.lang === locale) || 
                    voices.find(v => v.lang.startsWith(langCode)) ||
                    voices.find(v => v.name.toLowerCase().includes(langCode.toLowerCase()));
        
        // If a matching voice exists, use it. Otherwise, let the browser handle it using utterance.lang
        if (voice) {
            utterance.voice = voice;
        } else {
            console.warn("No native voice found for " + locale + ". Browser will try its best.");
            if (langCode !== 'en') {
               document.getElementById('wa-action-status').innerText = '⚠ Language Voice Missing';
            }
        }
        
        utterance.rate = 1.0; // Normal but fast enough
        utterance.pitch = 1.0;
        
        utterance.onstart = () => {
            document.getElementById('wa-status-dot').style.background = '#ff4b2b';
            document.getElementById('wa-action-status').innerText = 'Speaking (' + (voice ? voice.lang : 'Auto') + ')...';
        };
        
        utterance.onend = () => {
            document.getElementById('wa-status-dot').style.background = '#25d366';
            document.getElementById('wa-action-status').innerText = 'Ready';
        };
        
        window.speechSynthesis.speak(utterance);
    };

    // Click Detection Logic
    document.addEventListener('click', function(e) {
        // Broad message detection
        let container = e.target.closest("[data-testid='msg-container'], .message-in, .message-out");
        if (container) {
            let text = "";
            let selectable = container.querySelector('.selectable-text, span.copyable-text');
            if (selectable) {
                text = selectable.innerText;
            } else {
                // Fallback: search for first span with text
                let spans = container.querySelectorAll('span');
                for (let s of spans) {
                    if (s.innerText.trim().length > 0) {
                        text = s.innerText;
                        break;
                    }
                }
            }

            if (text && text.trim().length > 0) {
                const targetCode = document.getElementById('wa-target-lang').value;
                window.waLastClickedMessage = {
                    text: text.trim(),
                    timestamp: Date.now(),
                    target: targetCode
                };
                
                const content = document.getElementById('wa-trans-content');
                content.style.opacity = '0.5';
                content.innerHTML = '<i>Processing...</i>';
                document.getElementById('wa-action-status').innerText = 'Translating...';
            }
        }
    }, true);

    window.updateTranslation = function(original, translated, targetCode, audioBase64) {
        const content = document.getElementById('wa-trans-content');
        content.style.opacity = '1';
        content.innerHTML = `
            <div style="margin-bottom: 8px; font-size: 13px; color: #666; font-style: italic;">Original: "${original.length > 40 ? original.substring(0, 37) + '...' : original}"</div>
            <div style="color: #000; font-weight: 600; font-size: 18px;">${translated}</div>
        `;
        
        if (audioBase64) {
            try {
                const audio = new Audio("data:audio/mp3;base64," + audioBase64);
                audio.play();
                document.getElementById('wa-status-dot').style.background = '#ff4b2b';
                audio.onended = () => {
                    document.getElementById('wa-status-dot').style.background = '#25d366';
                };
            } catch (err) {
                console.error("Audio playback error:", err);
                window.speakMessage(translated, targetCode);
            }
        } else {
            window.speakMessage(translated, targetCode);
        }
    };
})();
"""

class WhatsAppTranslator:
    def __init__(self):
        self.driver = None
        self.last_timestamp = 0
        self.target_langs = TARGET_LANGUAGES
        self.voice_mapping = {
            "en": "en-US-GuyNeural",
            "hi": "hi-IN-MadhurNeural",
            "mr": "mr-IN-ManoharNeural",
            "ta": "ta-IN-ValluvarNeural",
            "te": "te-IN-MohanNeural"
        }

    async def get_audio_base64(self, text, lang_code):
        voice = self.voice_mapping.get(lang_code, "en-US-GuyNeural")
        communicate = edge_tts.Communicate(text, voice)
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        
        if audio_data:
            return base64.b64encode(audio_data).decode('utf-8')
        return None

    def setup_driver(self):
        print("🚀 Initializing Premium WhatsApp AI Engine (Audio Enabled)...")
        chrome_options = Options()
        profile_path = os.path.join(os.getcwd(), "whatsapp_profile")
        if not os.path.exists(profile_path):
            os.makedirs(profile_path)
            
        chrome_options.add_argument(f"user-data-dir={profile_path}")
        chrome_options.add_argument("--disable-notifications")
        chrome_options.add_argument("--remote-debugging-port=9222")
        chrome_options.add_argument("--log-level=3")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
        
        self.driver.get("https://web.whatsapp.com")
        print("✅ Session active. Please scan QR if not logged in.")

    def inject_script(self):
        import json
        lang_json = json.dumps(self.target_langs)
        script = f"window.waTargetLangs = {lang_json};" + INJECTED_SCRIPT
        try:
            self.driver.execute_script(script)
        except Exception:
            pass 

    def poll_for_messages(self):
        print("⚡ Engine Speed: Turbo (0.1s Polling)")
        while True:
            try:
                last_clicked = self.driver.execute_script("return window.waLastClickedMessage;")
                if last_clicked and last_clicked['timestamp'] > self.last_timestamp:
                    self.last_timestamp = last_clicked['timestamp']
                    text = last_clicked['text']
                    target_code = last_clicked['target']
                    
                    try:
                        translated = GoogleTranslator(source='auto', target=target_code).translate(text)
                        
                        # Generate high-quality audio
                        try:
                            audio_b64 = asyncio.run(self.get_audio_base64(translated, target_code))
                        except Exception as e:
                            print(f"TTS Error: {e}")
                            audio_b64 = None
                            
                        safe_orig = text.replace("'", "\\'").replace('"', '\\"').replace("\n", " ")
                        safe_trans = translated.replace("'", "\\'").replace('"', '\\"').replace("\n", " ")
                        audio_arg = f"'{audio_b64}'" if audio_b64 else 'null'
                        
                        # Push back to JS UI with audio
                        self.driver.execute_script(f"window.updateTranslation('{safe_orig}', '{safe_trans}', '{target_code}', {audio_arg})")
                    except Exception as trans_err:
                        print(f"Translation Error: {trans_err}")
                        self.driver.execute_script(f"window.updateTranslation('Error', 'Translation failed.', 'en', null)")

                is_loaded = self.driver.execute_script("return (window.whatsappTranslatorLoaded && !!document.getElementById('wa-translator-ui'));")
                if not is_loaded:
                    self.inject_script()

            except Exception:
                pass
            
            time.sleep(0.1) # 10x faster polling



    def start(self):
        self.setup_driver()
        # Wait for WhatsApp to load
        print("Waiting for WhatsApp Web to load...")
        time.sleep(1) 
        
        # Start polling in a separate thread or just run here
        self.poll_for_messages()

if __name__ == "__main__":
    bot = WhatsAppTranslator()
    bot.start()
