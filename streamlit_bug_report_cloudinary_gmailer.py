import streamlit as st
import os
import smtplib

import cloudinary
import cloudinary.uploader
import io

from dotenv import load_dotenv
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from PIL import Image

# 환경변수 로딩
load_dotenv()
SEND_REPORT_EMAIL = True
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "False").lower() == "true"
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
TO_EMAILS = [email.strip() for email in os.getenv("TO_EMAILS", "").split(",") if email.strip()]
CLOUDINARY_CLOUD_NAME = os.getenv('CLOUDINARY_CLOUD_NAME')
CLOUDINARY_API_KEY = os.getenv('CLOUDINARY_API_KEY')
CLOUDINARY_API_SECRET = os.getenv('CLOUDINARY_API_SECRET')

# Cloudinary 설정
cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET
)

# 수신자 옵션 만들기 (이름 <이메일> or 이메일)
email_options = []
for entry in TO_EMAILS:
    if "<" in entry and ">" in entry:
        name = entry.split("<")[0].strip()
        addr = entry.split("<")[1].replace(">", "").strip()
        label = f"{name} ({addr})"
        email_options.append((label, addr))
    else:
        email_options.append((entry, entry))

email_labels = [label for label, addr in email_options]
email_addrs = [addr for label, addr in email_options]

def compress_image_to_target_size(image_file, target_mb=10, min_quality=30):
    # target_mb: 제한 용량(MB), min_quality: 최소 허용 JPEG 품질
    image = Image.open(image_file)
    quality = 90
    buffer = io.BytesIO()
    image = image.convert('RGB')  # PNG → JPEG 변환 시 필요

    while quality >= min_quality:
        buffer.seek(0)
        buffer.truncate()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        size_mb = buffer.getbuffer().nbytes / (1024 * 1024)
        if size_mb <= target_mb:
            buffer.seek(0)
            return buffer, size_mb, quality
        quality -= 10
    # 최저 품질에도 10MB 초과면 resize까지 시도
    width, height = image.size
    while size_mb > target_mb and (width > 512 and height > 512):
        width = int(width * 0.8)
        height = int(height * 0.8)
        image = image.resize((width, height), Image.LANCZOS)
        buffer.seek(0)
        buffer.truncate()
        image.save(buffer, format="JPEG", quality=min_quality, optimize=True)
        size_mb = buffer.getbuffer().nbytes / (1024 * 1024)
        if size_mb <= target_mb:
            buffer.seek(0)
            return buffer, size_mb, min_quality
    buffer.seek(0)
    return buffer, size_mb, min_quality  # 마지막 결과 반환 (용량 넘으면 안내)

# Cloudinary 업로드 및 라벨링
def upload_files_to_cloudinary(files):
    uploaded_links = []
    errors = []  # 에러 메시지 저장용
    screenshot_count = 1
    video_count = 1

    image_exts = ('.png', '.jpg', '.jpeg', '.pdf')
    video_exts = ('.mp4', '.mov', '.webm', '.avi', '.mkv', '.mpeg', '.mpg', '.wmv', '.flv', '.gif', '.edf', '.webm')

    for file in files:
        fname = file.name if hasattr(file, 'name') else os.path.basename(file)
        ext = '.' + fname.lower().rsplit('.', 1)[-1]

        if ext in image_exts:
            resource_type = "image" if ext != ".pdf" else "raw"
            label = f"SCREENSHOT{str(screenshot_count)}"
            screenshot_count += 1
        elif ext in video_exts:
            resource_type = "video"
            label = f"VIDEO{str(video_count)}"
            video_count += 1
        else:
            resource_type = "raw"
            label = f"FILE_{fname}"

        try:
            file.seek(0)
            result = cloudinary.uploader.upload_large(
                file,
                resource_type=resource_type,
                folder="bug_report_files",
                use_filename=True,
                unique_filename=False
            )
            uploaded_links.append((label, result['secure_url']))
            print(f"✅ Uploaded: {fname} as {label}")
        except Exception as e:
            error_msg = str(e)
            print(f"❌ Failed to upload {fname}: {error_msg}")
            # Cloudinary 에러 메시지에서 파일 크기 정보 파싱
            # 예: 'File size too large. Got 79655928. Maximum is 10485760.'
            mb = lambda b: round(int(b) / (1024 * 1024), 2)
            import re
            got, maximum = None, None
            match = re.search(r'Got (\d+).+Maximum is (\d+)', error_msg)
            if match:
                got, maximum = match.group(1), match.group(2)
            if got and maximum:
                errors.append(
                    f"❌ <b>{fname}</b>: 파일 용량 {mb(got)}MB (최대 {mb(maximum)}MB 제한, Cloudinary 무료/기본 플랜)"
                )
            else:
                errors.append(f"❌ <b>{fname}</b>: 업로드 실패 - {error_msg}")

    return uploaded_links, errors

# 이메일 전송(첨부 NO, 하이퍼링크 ONLY)
def send_bug_report_via_smtp(subject, html_body, uploaded_links, to_emails):
    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject

    # 첨부파일 하이퍼링크 추가
    if uploaded_links:
        links_html = """
        <h3 style="margin-top:24px;">
            <img src="https://cdn-icons-png.flaticon.com/512/724/724933.png" width="20" style="vertical-align:middle;"/> 
            <b>Attachments</b>
        </h3>
        <ul>
        """
        for label, url in uploaded_links:
            links_html += f'<li><a href="{url}" target="_blank">{label}</a></li>'
        links_html += "</ul>"
        # 본문 </body> 앞에 넣기
        html_body = html_body.replace("</body>", links_html + "</body>")

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    if SMTP_USE_SSL:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to_emails, msg.as_string())
    else:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to_emails, msg.as_string())

# ---- Streamlit 폼 ----

st.title("🪲 Customer Bug Report Form")

with st.form("bug_form"):
    # --- 수신자 선택 드롭다운 추가 ---
    selected_recipient_idx = st.selectbox(
        "받는 사람(수신자)",
        options=list(range(len(email_labels))),
        format_func=lambda idx: email_labels[idx]
    )
    summary = st.text_area("Summary", help="요약/핵심 증상을 적어주세요.")
    steps = st.text_area("Steps to Reproduce", help="재현 순서를 단계별로 적어주세요.")
    expected = st.text_area("Expected Result", help="정상 동작을 적어주세요.")
    actual = st.text_area("Actual Result", help="실제 관찰 결과를 적어주세요.")
    notes = st.text_area("Additional Notes", help="추가 참고사항, 환경 정보 등")
    uploaded_files = st.file_uploader("Attachments (이미지/동영상 등 여러개)", type=["png", "jpg", "jpeg", "mp4", "mov", "gif", "pdf", "edf"], accept_multiple_files=True)
    submitted = st.form_submit_button("제출하기")

if submitted:
    if not summary or not steps or not expected or not actual:
        st.warning("모든 주요 입력란을 작성해주세요.")
    elif not SEND_REPORT_EMAIL:
        st.error("이메일 전송이 비활성화 되어있습니다. .env 설정을 확인하세요.")
    else:
        html_body = f"""
            <!DOCTYPE html>
            <html>
            <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, Helvetica, sans-serif; color: #222; font-size: 15px; line-height: 1.6; }}
                h2, h3 {{ margin-top: 18px; margin-bottom: 8px; }}
                ul, ol {{ margin-bottom: 14px; }}
                li {{ margin-bottom: 6px; }}
                .emoji {{ font-size: 18px; vertical-align: middle; }}
                .section-title {{ font-weight: bold; margin-top: 20px; }}
            </style>
            </head>
            <body>
            <h2><span class="emoji">🧭</span> <b>Summary</b></h2>
            <div>{summary}</div>
            <h3><span class="emoji">🦎</span> <b>Steps to Reproduce</b></h3>
            <ol>{"".join(f"<li>{line}</li>" for line in steps.splitlines() if line.strip())}</ol>
            <h3><span class="emoji">✅</span> <b>Expected Result</b></h3>
            <ul><li>{expected}</li></ul>
            <h3><span class="emoji">❌</span> <b>Actual Result</b></h3>
            <ul><li>{actual}</li></ul>
            <h3><span class="emoji">📝</span> <b>Additional Notes</b></h3>
            <ul><li>{notes}</li></ul>
            </body>
            </html>
            """

        # 선택된 수신자
        selected_to_email = [email_addrs[selected_recipient_idx]]

        with st.spinner("이메일을 전송하는 중입니다. 잠시만 기다려주세요..."):
            try:
                uploaded_links = []
                if uploaded_files:
                    files_to_upload = uploaded_files if isinstance(uploaded_files, list) else [uploaded_files]
                    uploaded_links, upload_errors = upload_files_to_cloudinary(files_to_upload)
                    if upload_errors:
                        # 여러 줄로 안내 (HTML 지원)
                        st.error('\n'.join(upload_errors))

                send_bug_report_via_smtp(
                    subject=f"[BUG REPORT] {summary}",
                    html_body=html_body,
                    uploaded_links=uploaded_links,
                    to_emails=selected_to_email,
                )
                st.success("✅ 이메일이 성공적으로 전송되었습니다! (첨부파일은 링크로 제공됩니다.)")
                st.balloons()
            except Exception as e:
                st.error(f"❌ 이메일 전송 실패: {e}")

