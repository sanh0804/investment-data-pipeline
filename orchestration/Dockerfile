FROM apache/airflow:3.3.2-python3.12
COPY --chown=airflow:root orchestration/requirements.txt orchestration/constraints-3.3.2-python3.12.txt /opt/requirements/
RUN pip install --no-cache-dir -r /opt/requirements/requirements.txt \
    --constraint /opt/requirements/constraints-3.3.2-python3.12.txt && pip check
COPY --chown=airflow:root pipeline /opt/project/pipeline
COPY --chown=airflow:root orchestration/dags /opt/airflow/dags
COPY --chown=airflow:root orchestration/initialize.py /opt/project/initialize.py
ENV PYTHONPATH=/opt/project
