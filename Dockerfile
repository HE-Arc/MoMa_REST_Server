FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
LABEL authors="antoine.lestrade"

# Set the working directory in the container to /app
WORKDIR /app

# Install dependencies
RUN apt-get update \
    && apt-get install -y bison build-essential \
    curl flex git libassimp-dev libbz2-dev libc6-dev \
    libffi-dev libgdbm-dev libncursesw5-dev libsqlite3-dev \
    libssl-dev libxml2-dev tk-dev zlib1g-dev


#RUN git clone https://gitlab-etu.ing.he-arc.ch/isc/general/motion-machine/3d-animation-db/synthetic-animation-data.git
#RUN mkdir /app/animations
#RUN mkdir /app/animations/pca
#RUN mkdir /app/animations/locomotion
#COPY animations/locomotion/locomotion.pkl /app/animations/locomotion
#RUN mv /app/synthetic-animation-data/animations/* /app/animations/pca/
#RUN rm -r /app/synthetic-animation-data


# Install any needed packages specified in requirements.txt
COPY pyproject.toml /app
RUN uv sync

# Make port 80 available to the world outside this container
EXPOSE 9810

# Add the current directory contents into the container at /app
COPY /src /app

#CMD ["python", "assimp_test.py"]
#CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "80"]
CMD ["uv", "run", "main.py"]
#CMD ["scalene", "src/main.py"]
