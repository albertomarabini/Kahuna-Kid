# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .
# --- FOR LOCAL TESTING ONLY (if you use a SA key file) ---
# If you are testing locally with a service account key file,
# copy it here and set GOOGLE_APPLICATION_CREDENTIALS.
# For Cloud Run deployment, you DO NOT need these lines.
# COPY sa-key.json /app/sa-key.json
# Set environment variable to point to the key (for client libraries)
# ENV GOOGLE_APPLICATION_CREDENTIALS=/app/sa-key.json
# Define the port that the container will listen on
ENV PORT 8080
EXPOSE $PORT

# Run the application
# Cloud Run typically expects your application to listen on the port specified by the PORT environment variable
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
CMD ["sh", "-lc", "gunicorn -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:${PORT:-8080} --timeout ${GUNICORN_TIMEOUT:-120} --graceful-timeout ${GUNICORN_GRACEFUL_TIMEOUT:-30} --keep-alive ${GUNICORN_KEEPALIVE:-5} --access-logfile - --error-logfile - --log-level info --capture-output main:app"]


