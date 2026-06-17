#!/usr/bin/env python3
import json
import os
import sys
import logging

logging.basicConfig(level=logging.INFO)

def fetch_recent_facts(critic_diagnosis):
    try:
        with open("/home/ubuntu/.nanobot/facts_database.json", "r") as file:
            facts = json.load(file)
        recent_facts = [fact for fact in facts if fact['date'] >= critic_diagnosis['date']]
        return recent_facts
    except FileNotFoundError:
        logging.error("Facts database not found.")
        return []
    except json.JSONDecodeError:
        logging.error("Facts database is not a valid JSON.")
        return []

def validate_critique(critic_diagnosis):
    recent_facts = fetch_recent_facts(critic_diagnosis)
    if recent_facts:
        for fact in recent_facts:
            if fact['fact'] == critic_diagnosis['fact']:
                return True
    return False

def main():
    if len(sys.argv) != 2:
        logging.error("Usage: python validate_critique.py <diagnosis_json>")
        sys.exit(1)

    try:
        critic_diagnosis = json.loads(sys.argv[1])
        if validate_critique(critic_diagnosis):
            logging.info("Critique validated.")
        else:
            logging.info("Critique not validated.")
    except json.JSONDecodeError:
        logging.error("Invalid JSON provided.")
        sys.exit(1)

if __name__ == "__main__":
    main()