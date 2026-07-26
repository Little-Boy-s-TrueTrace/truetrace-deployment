#!/bin/bash

echo "=================================================="
echo "    TRUETRACE KAFKA TOPICS INITIALIZER            "
echo "=================================================="

# Wait for Kafka brokers to be ready
echo "Waiting for Kafka broker (kafka-1:29092) to respond..."
until /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka-1:29092 --list > /dev/null 2>&1; do
  echo "Kafka is not fully active yet. Retrying in 3 seconds..."
  sleep 3
done

echo "Kafka is responsive! Beginning topic provisioning..."

# Array of all required TrueTrace compliance and streaming topics
TOPICS=(
  "truetrace.kyc.submissions"
  "truetrace.transactions"
  "truetrace.findings.deepfake"
  "truetrace.findings.money_trail"
  "truetrace.reports.str"
  "truetrace.alerts"
)

# Provision topics
for topic in "${TOPICS[@]}"; do
  echo "Provisioning topic: $topic"
  /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka-1:29092 \
    --create \
    --if-not-exists \
    --topic "$topic" \
    --partitions 3 \
    --replication-factor 3
done

echo "=================================================="
echo "  All ${#TOPICS[@]} Kafka topics initialized successfully!   "
echo "=================================================="
