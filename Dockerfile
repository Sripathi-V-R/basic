# Use official lightweight Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install Chrome and dependencies
RUN apt-get update && apt-get install -y \
    wget unzip curl gnupg xvfb \
    && wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt install -y ./google-chrome-stable_current_amd64.deb \
    && rm google-chrome-stable_current_amd64.deb

# Install ChromeDriver
RUN CHROME_VERSION=$(google-chrome --version | awk '{print $3}') && \
    wget -O /tmp/chromedriver.zip "https://chromedriver.storage.googleapis.com/$(echo $CHROME_VERSION | cut -d. -f1)/chromedriver_linux64.zip" || true && \
    unzip /tmp/chromedriver.zip -d /usr/local/bin || true

# Copy dependencies file and install packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all files
COPY . .

# Expose Streamlit port
EXPOSE 8501

# Start Streamlit app
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
