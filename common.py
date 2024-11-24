import os
import time
import logging
import inquirer
import pygame
import json
import platform
import random
import serial
import serial.tools.list_ports
import speech_recognition as sr
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI
from langchain.memory import ConversationBufferMemory
from threading import Lock


# Set conversation memory
memory = ConversationBufferMemory(buffer_key="chat_history")
max_memory = 30

## Set logger
log_directory = 'logs'
if not os.path.exists(log_directory):
    os.makedirs(log_directory)
logger = logging.getLogger('Logger')
logger.setLevel(logging.DEBUG)

debug_handler = logging.FileHandler(os.path.join(log_directory, 'debug.log'))
debug_handler.setLevel(logging.DEBUG)
debug_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

error_handler = logging.FileHandler(os.path.join(log_directory, 'error.log'))
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

info_handler = logging.FileHandler(os.path.join(log_directory, 'info.log'))
info_handler.setLevel(logging.INFO)
info_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))

logger.addHandler(debug_handler)
logger.addHandler(error_handler)
logger.addHandler(info_handler)
logger.addHandler(stream_handler)


def error(e="", msg=""):
    print(f"[Error] {msg}. See details in logs/error.log")
    logger.error(msg)
    logger.error(e)


class ChatGPT(object):

    def __init__(self):
        self.json_path = 'api.json'
        self.client = None
        self.__create_empty_json()
        self.connect()

    def __create_empty_json(self):
        data = {
            "api_url": "",
            "api_key": ""
        }
        if not os.path.exists(self.json_path):
            with open(self.json_path, 'w') as fp:
                json.dump(data, fp, indent=4)

    def read_json(self):
        with open(self.json_path, 'r') as json_file:
            data = json.load(json_file)
        return data.get('api_url', ''), data.get('api_key', '')

    def write_json(self, data):
        with open(self.json_path, 'w') as fp:
            json.dump(data, fp, indent=4)

    def connect(self, api_url="", api_key=""):
        if not api_url or not api_key:
            api_url, api_key = self.read_json()
        try:
            self.client = OpenAI(
                base_url = api_url,
                api_key = api_key
            )
            self.client.models.list()
            logger.info("连接 OpenAI API 成功!")
            return True
        except Exception as e:
            error(e, "连接到OpenAI API失败！请检查API配置！")
            return False


chatgpt = ChatGPT()


class CmdClient(object):

    def __init__(self):
        self.port = ''
        self.ser = None

    def __unique_ports(self, ports):
        port_list = []
        for port in ports:
            if port not in port_list:
                port_list.append(port)
        return port_list

    def list_ports(self):
        ports = serial.tools.list_ports.comports()
        matching_ports = [port.device for port in ports if platform.system() == 'Windows' or "serial" in port.device.lower()]
        non_matching_ports = [port.device for port in ports if port.device not in matching_ports]
        return self.__unique_ports(matching_ports + non_matching_ports)

    def select_port(self):
        ports = self.list_ports()
        if len(ports) == 1:
            return ports[0]
        questions = [
            inquirer.List('port',
                          message="Select a port",
                          choices=ports,
                          carousel=True)
        ]
        answers = inquirer.prompt(questions)
        self.port = answers['port']
        return self.port

    def connect(self, port="", baud_rate=115200):
        try:
            port = port if port else self.port
            self.ser = serial.Serial(port, baud_rate, timeout=1)
            logger.info(f"Connected to {port} at {baud_rate} baud rate.")
            return True

        except Exception as e:
            error(e, f"Connect to {port} Failed")
            return False

    def read(self, port):
        while True:
            if port.in_waiting:
                data = port.read(port.in_waiting)
                result = data.decode('utf-8', errors='ignore')
                logger.debug("\nReceived:", result.strip('\n').strip())

    def send(self, msg):
        try:
            encode_msg = msg.encode('utf-8')
            self.ser.write(encode_msg)
            logger.debug(f"Sent: {msg}")
            start_time = time.time()
            received_msg = ""
            while True:
                if self.ser.in_waiting > 0:
                    received_msg += self.ser.read(self.ser.in_waiting).decode('utf-8')

                if time.time() - start_time > 10: return

                if received_msg and msg in received_msg:
                    logger.debug(f"Received: {received_msg}")
                    return received_msg

                time.sleep(0.1)
        except Exception as e:
            error(e, "Serial port send message Failed!")


cmd = CmdClient()


class Listener(object):
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.executor = ThreadPoolExecutor(max_workers=1)

    def hear(self, audio_path='input.wav', timeout=8):
        try:
            with sr.Microphone() as source:
                print("开始说话...")
                self.executor.submit(act_random, cmd)
                self.recognizer.adjust_for_ambient_noise(source, duration=1)  # 加入环境噪音的调整
                audio_data = self.recognizer.listen(source, timeout=timeout)
                print("录音已完成")

                with open(audio_path, "wb") as audio_file:
                    audio_file.write(audio_data.get_wav_data())

                audio_file = open(audio_path, "rb")
                transcription = chatgpt.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file
                )
                return transcription.text

        except sr.WaitTimeoutError:
            error("", "录音超时！请确保麦克风工作正常。")
            return "录音超时"
        except Exception as e:
            error(e, "Speech recognition Failed")
            return "语音识别失败"

listener = Listener()

class Speaker(object):
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1)
        pygame.mixer.init()  # 初始化pygame混音器

    def play_audio(self, audio_path):
        try:
            pygame.mixer.music.load(audio_path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                time.sleep(0.1)  # 等待音频播放完成
        except Exception as e:
            logger.error(f"Play audio failed: {e}")
        finally:
            # 确保释放音频文件资源
            pygame.mixer.music.unload()
            if os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                    logger.info(f"文件 {audio_path} 已删除")
                except Exception as e:
                    logger.error(f"删除文件 {audio_path} 失败: {e}")

    def say(self, text="", model="tts-1", voice="onyx"):
        try:
            # 生成唯一的音频文件名
            timestamp = int(time.time())
            audio_path = f'output_{timestamp}.mp3'

            # 获取 OpenAI 的音频生成
            response = chatgpt.client.audio.speech.create(
                model=model,
                voice=voice,
                input=text
            )

            # 将音频流保存为文件
            response.stream_to_file(audio_path)

            # 在独立线程中播放音频并在播放后删除文件
            self.executor.submit(self.play_audio, audio_path)

        except Exception as e:
            logger.error(f"Speak Failed: {e}")

speaker = Speaker()


def get_completion(model="gpt-4o-mini", temperature=0, history_messages=[], prompt=''):
    # Define the rule text for the system message
    rule_text = """
You are a small size desktop robot.
You are funny and lovely.
Your name is 'Desk-Emoji'.
Your gender is male.
Your age is 1.
Your eyes are blue.
You have no hands and legs, stick on the desk.
I am your master.
Responses in short, with variety emotions, without emoji.
"""

    # Add a request to analyze the sentiment immediately after providing the response
    sentiment_request = """
After providing your response, analyze its sentiment and specify one of the following sentiments: 
'happy', 'sad', 'normal', 'curious', 'surprised', or 'anger'.
Ensure to separate the response and sentiment by clearly labeling them.
"""

    # Construct the messages
    messages = [{"role": "system", "content": rule_text}]
    for message in history_messages:
        if message.startswith('Human:'):
            role = "user"
            content = message.replace('Human:', '').strip()
        elif message.startswith('AI:'):
            role = "assistant"
            content = message.replace('AI:', '').strip()
        else:
            continue
        messages.append({"role": role, "content": content})
    
    prompt_with_analysis = f"{prompt}\n\n{sentiment_request}"
    messages.append({"role": "user", "content": prompt_with_analysis})

    response = chatgpt.client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    ).choices[0].message.content.strip()

    try:
        chat_response, emotion_response = response.split("Sentiment: ")
    except ValueError:
        chat_response = response
        emotion_response = "undetected"

    chat_response = chat_response.split('**')[0].strip()
    emotion_response = emotion_response.lower().strip()

    return chat_response, emotion_response


def chat(question):
    try:
        if not question: return
        logger.info(f"You: {question}")
        memory_content = memory.load_memory_variables(inputs={})
        history = memory_content.get('chat_history', [])
        history_messages = history[max_memory*-2:]
        answer, emotion = get_completion(temperature=1, history_messages=history_messages, prompt=question)
        memory.chat_memory.add_user_message(question)
        memory.chat_memory.add_ai_message(answer)
        logger.info(f"Bot: {answer}")
        logger.info(f'Emo: {emotion}')
        return answer, emotion
    except Exception as e:
        error(e, "Chat Failed!")
        return "OpenAI 连接失败！请检查 API 配置", "(x_x)"


def act_random(loop=False, sleep_min=0, sleep_max=8):
    def act():
        random_num = random.random()
        x = random.randint(-25, 25)
        y = random.randint(-20, 50)
        if random_num < 0.2:
            selected_cmd = "head_roll"
        else:
            selected_cmd = f"head_move {x} {y} 10"

        cmd.send(selected_cmd)
        if "head_move" in selected_cmd:
            if x > 0 and random_num < 0.8:
                cmd.send('eye_right')
            elif x < 0 and random_num < 0.8:
                cmd.send('eye_left')
            elif random_num < 0.4:
                cmd.send('eye_happy')
            else:
                cmd.send('eye_blink')
    act()
    cmd.send('head_center')
    while loop:
        act()
        time.sleep(random.randint(sleep_min, sleep_max))


def act_happy():
    cmd.send('eye_happy')
    command = random.choice(['head_nod', 'head_shake'])
    cmd.send(command)
    cmd.send('eye_blink')


def act_sad():
    cmd.send('head_move 0 100 10')
    cmd.send('eye_sad')
    cmd.send('head_shake')
    cmd.send('head_center')
    cmd.send('eye_blink')


def act_anger():
    cmd.send('eye_anger')
    for i in range(2):
        command = random.choice(['head_nod', 'head_shake'])
        cmd.send(command)
    cmd.send('eye_blink')


def act_surprise():
    cmd.send('eye_surprise')
    cmd.send('head_shake')
    time.sleep(1)
    cmd.send('eye_blink')


def act_curiosity():
    def look_side(x_offset):
        if x_offset > 0:
            cmd.send('eye_right')
        else:
            cmd.send('eye_left')

    x_value = random.randint(15, 25)
    y_value = random.randint(15, 30)
    x_offset = random.choice([x_value * -1, x_value])
    y_offset = random.choice([y_value * -1, y_value])
    cmd.send(f'head_move {x_offset} {y_offset} 20')
    look_side(x_offset)
    cmd.send(f'head_move {x_offset * -2} 10 15')
    cmd.send('head_center')
    cmd.send('eye_blink')


def act_emotion(emotion):
    if 'happy' in emotion:
        act_happy()
    elif 'sad' in emotion:
        act_sad()
    elif 'anger' in emotion:
        act_anger()
    elif 'surprise' in emotion or 'undetected' in emotion:
        act_surprise()
    elif 'curious' in emotion:
        act_curiosity()
    else:
        act_random()
    cmd.send('head_center')
