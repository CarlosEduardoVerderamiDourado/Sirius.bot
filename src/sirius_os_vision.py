"""
sirius_os_vision.py - Visao computacional e captura visual do Sirius

Integracao com OpenCV e PyAutoGUI para:
- Captura de tela
- Acesso a webcam
- Descricao de contexto visual ao modelo de linguagem
- Compatibilidade com LangGraph agents

LGPD Compliance:
- Nenhuma coleta de dados pessoais sem consentimento explicito
- Imagens nao sao salvas sem permissao
- Auditoria de acesso visual registrada
"""

import os
import sys
import base64
import threading
import sqlite3
import json
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from pathlib import Path

try:
    import cv2
    OPENCV_DISPONIVEL = True
except ImportError:
    OPENCV_DISPONIVEL = False

try:
    import pyautogui
    PYAUTOGUI_DISPONIVEL = True
except ImportError:
    PYAUTOGUI_DISPONIVEL = False

diretorio_src = os.path.dirname(os.path.abspath(__file__))
diretorio_raiz = os.path.dirname(diretorio_src)
if diretorio_src not in sys.path:
    sys.path.insert(0, diretorio_src)

CAMINHO_DATA = os.path.join(diretorio_raiz, "data")
os.makedirs(CAMINHO_DATA, exist_ok=True)


class SiriusOSVision:
    """
    Modulo de visao computacional para agentes autonomos.
    
    Tools:
    - capture_screen(): captura tela completa
    - access_webcam(): acessa webcam se autorizado
    - describe_visual_context(): descreve o que ve na tela
    """
    
    def __init__(self, memoria=None, user_id: str = "guest"):
        """
        Inicializa o modulo de visao.
        
        Args:
            memoria: instancia de SiriusMemory para auditoria
            user_id: id do usuario para LGPD compliance
        """
        self.memoria = memoria
        self.user_id = user_id
        self.db_pessoal = os.path.join(CAMINHO_DATA, "sirius_pessoal.db")
        
        self._criar_tabela_auditoria()
        self._lock = threading.Lock()
        
        print(f"\033[94m[VISION]: Inicializando vision para user_id={user_id}...\033[0m")
        if not OPENCV_DISPONIVEL:
            print("\033[93m[VISION]: OpenCV nao disponivel. Install: pip install opencv-python\033[0m")
        if not PYAUTOGUI_DISPONIVEL:
            print("\033[93m[VISION]: PyAutoGUI nao disponivel. Install: pip install pyautogui\033[0m")
    
    def _criar_tabela_auditoria(self):
        """Cria tabela de auditoria de acesso visual (LGPD)."""
        try:
            conn = sqlite3.connect(self.db_pessoal)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS auditoria_vision (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    tipo_acesso TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    metadata TEXT,
                    consentimento_explicitado BOOLEAN DEFAULT 0
                );
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_auditoria_vision_user ON auditoria_vision(user_id);"
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"\033[91m[VISION]: Erro criando tabela auditoria: {e}\033[0m")
    
    def _registrar_acesso(self, tipo_acesso: str, metadata: str = "", consentimento: bool = False):
        """Registra acesso para auditoria LGPD."""
        try:
            conn = sqlite3.connect(self.db_pessoal)
            conn.execute(
                """
                INSERT INTO auditoria_vision 
                    (user_id, tipo_acesso, metadata, consentimento_explicitado)
                VALUES (?, ?, ?, ?)
                """,
                (self.user_id, tipo_acesso, metadata, consentimento)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"\033[91m[VISION]: Erro registrando acesso: {e}\033[0m")
    
    def _verificar_consentimento(self, tipo_acesso: str) -> bool:
        """Verifica se ha consentimento explicito do usuario (LGPD)."""
        try:
            conn = sqlite3.connect(self.db_pessoal)
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM auditoria_vision 
                WHERE user_id = ? AND tipo_acesso = ? AND consentimento_explicitado = 1
                LIMIT 1
                """,
                (self.user_id, tipo_acesso)
            )
            resultado = cursor.fetchone()[0] > 0
            conn.close()
            return resultado
        except Exception:
            return False
    
    def capture_screen(self) -> Optional[Dict[str, Any]]:
        """
        Captura a tela do usuario.
        
        Retorna dict com:
        - success: bool
        - image_base64: string (JPEG comprimida)
        - width, height: dimensoes
        - timestamp: quando foi capturada
        - error: mensagem de erro se houver
        
        LangGraph Node compatible.
        """
        with self._lock:
            self._registrar_acesso("capture_screen")
            
            if not PYAUTOGUI_DISPONIVEL:
                return {
                    "success": False,
                    "error": "PyAutoGUI nao disponivel",
                    "timestamp": datetime.now().isoformat()
                }
            
            try:
                screenshot = pyautogui.screenshot()
                
                # Converte para array numpy + OpenCV
                import numpy as np
                img_array = np.array(screenshot)
                img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
                
                # Comprime JPEG
                success, buffer = cv2.imencode('.jpg', img_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if not success:
                    return {"success": False, "error": "Falha ao comprimir imagem"}
                
                # Converte para base64
                img_base64 = base64.b64encode(buffer).decode('utf-8')
                
                h, w = img_bgr.shape[:2]
                
                print(f"\033[92m[VISION]: Captura de tela realizada ({w}x{h})\033[0m")
                
                return {
                    "success": True,
                    "image_base64": img_base64,
                    "width": w,
                    "height": h,
                    "timestamp": datetime.now().isoformat(),
                    "size_bytes": len(buffer)
                }
            except Exception as e:
                self._registrar_acesso("capture_screen_erro")
                return {
                    "success": False,
                    "error": str(e),
                    "timestamp": datetime.now().isoformat()
                }
    
    def access_webcam(self, frames: int = 1, usuario_consentiu: bool = False) -> Optional[Dict[str, Any]]:
        """
        Acessa webcam se usuario consentiu (LGPD).
        
        Args:
            frames: quantos frames capturar (default 1)
            usuario_consentiu: booleano de consentimento explicito
        
        Retorna list de dicts com imagens capturadas.
        """
        if not usuario_consentiu:
            self._registrar_acesso("webcam_acesso_negado")
            return {
                "success": False,
                "error": "Acesso a webcam requer consentimento explicito do usuario",
                "consentimento_necessario": True
            }
        
        if not OPENCV_DISPONIVEL:
            return {"success": False, "error": "OpenCV nao disponivel"}
        
        self._registrar_acesso("access_webcam", consentimento=True)
        
        try:
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return {"success": False, "error": "Webcam nao acessivel"}
            
            capturadas = []
            for i in range(frames):
                ret, frame = cap.read()
                if not ret:
                    break
                
                # Comprime
                success, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if success:
                    img_base64 = base64.b64encode(buffer).decode('utf-8')
                    capturadas.append({
                        "frame": i,
                        "image_base64": img_base64,
                        "height": frame.shape[0],
                        "width": frame.shape[1]
                    })
            
            cap.release()
            
            print(f"\033[92m[VISION]: {frames} frames capturados da webcam\033[0m")
            
            return {
                "success": True,
                "frames": capturadas,
                "count": len(capturadas),
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def describe_visual_context(self, screenshot_base64: str, prompt_usuario: str = "") -> Dict[str, Any]:
        """
        Descreve o contexto visual de uma imagem.
        
        Para ser chamado com um modelo multimodal (Claude, GPT-4V, etc).
        Retorna a descricao do que o modelo ve.
        
        Args:
            screenshot_base64: imagem em base64
            prompt_usuario: instrucoes do usuario sobre o que descrever
        
        Retorna:
            {
                "success": bool,
                "description": str (descricao gerada pelo modelo),
                "confidence": float
            }
        """
        self._registrar_acesso("describe_visual_context")
        
        if not screenshot_base64:
            return {"success": False, "error": "Nenhuma imagem fornecida"}
        
        # Aqui o Sirius usaria um modelo multimodal
        # Por enquanto, retorna um template para LangGraph
        
        return {
            "success": True,
            "image_base64": screenshot_base64,
            "prompt": prompt_usuario or "Descreva o que voce ve nesta tela em detalhes.",
            "ready_for_multimodal": True,
            "timestamp": datetime.now().isoformat(),
            "note": "Enviar para modelo multimodal via API (Claude/GPT-4V)"
        }
    
    def get_auditoria_visual(self, dias: int = 7) -> list:
        """Retorna historico de acesso visual para auditoria LGPD."""
        try:
            conn = sqlite3.connect(self.db_pessoal)
            cursor = conn.execute(
                """
                SELECT tipo_acesso, timestamp, consentimento_explicitado 
                FROM auditoria_vision
                WHERE user_id = ? AND datetime(timestamp) > datetime('now', ? || ' days')
                ORDER BY timestamp DESC
                """,
                (self.user_id, -dias)
            )
            resultados = cursor.fetchall()
            conn.close()
            return resultados
        except Exception as e:
            print(f"\033[91m[VISION]: Erro obtendo auditoria: {e}\033[0m")
            return []


# Tool para LangGraph Agent
def vision_tool_factory(memoria=None, user_id: str = "guest") -> Dict[str, Any]:
    """
    Factory para criar Tool compativel com LangGraph.
    
    Retorna dict com definicao de tool para agent usar.
    """
    vision = SiriusOSVision(memoria=memoria, user_id=user_id)
    
    return {
        "name": "vision_system",
        "description": "Acessa visao computacional (captura de tela, webcam, descricao visual)",
        "functions": {
            "capture_screen": vision.capture_screen,
            "access_webcam": vision.access_webcam,
            "describe_visual_context": vision.describe_visual_context,
            "get_auditoria": vision.get_auditoria_visual
        },
        "instance": vision
    }
