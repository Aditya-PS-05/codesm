"""Configuration validator utilities for codesm.

Provides functions to validate and test configuration files,
ensuring that all settings are correct and compatible.
"""

from pathlib import Path
from typing import Dict, List, Tuple, Optional
from pydantic import ValidationError

from .schema import Config, ProviderConfig, ModelConfig, SafetyConfig


class ConfigValidator:
    """Utility class for validating codesm configurations."""

    @staticmethod
    def validate_config(config_dict: Dict) -> Tuple[bool, List[str]]:
        """Validate a configuration dictionary.

        Args:
            config_dict: Configuration dictionary to validate

        Returns:
            Tuple of (is_valid, error_messages)
        """
        errors: List[str] = []
        
        try:
            Config(**config_dict)
            return True, []
        except ValidationError as e:
            for error in e.errors():
                field = '.'.join(str(x) for x in error['loc'])
                msg = error['msg']
                errors.append(f"{field}: {msg}")
            return False, errors

    @staticmethod
    def validate_provider_config(provider_name: str, api_key: Optional[str] = None) -> Tuple[bool, str]:
        """Validate provider configuration.

        Args:
            provider_name: Name of the provider (anthropic, openai)
            api_key: API key to validate

        Returns:
            Tuple of (is_valid, message)
        """
        valid_providers = ["anthropic", "openai", "openrouter", "ollama"]
        
        if provider_name not in valid_providers:
            return False, f"Unknown provider: {provider_name}. Valid options: {', '.join(valid_providers)}"
        
        if provider_name != "ollama" and not api_key:
            return False, f"API key required for {provider_name}"
        
        if api_key and len(api_key.strip()) == 0:
            return False, "API key cannot be empty"
        
        return True, f"Valid configuration for {provider_name}"

    @staticmethod
    def validate_model_config(model: str, provider: str) -> Tuple[bool, str]:
        """Validate model configuration.

        Args:
            model: Model name/identifier
            provider: Provider name

        Returns:
            Tuple of (is_valid, message)
        """
        if not model or not model.strip():
            return False, "Model name cannot be empty"
        
        # Known models per provider (non-exhaustive)
        known_models = {
            "anthropic": ["claude-3-5-sonnet-20241022", "claude-opus-4-1", "claude-3-haiku-20240307"],
            "openai": ["gpt-4o", "gpt-4-turbo", "gpt-4o-mini"],
            "openrouter": None,  # Accepts various models
            "ollama": None,  # Custom models
        }
        
        if provider in known_models and known_models[provider]:
            if model not in known_models[provider]:
                return True, f"Model '{model}' not in known list. Proceeding anyway (verify model availability)."
        
        return True, f"Valid model configuration: {model}"

    @staticmethod
    def validate_safety_config(safety_config: Dict) -> Tuple[bool, List[str]]:
        """Validate safety and permission settings.

        Args:
            safety_config: Safety configuration dictionary

        Returns:
            Tuple of (is_valid, warnings/errors)
        """
        issues: List[str] = []
        
        # Warnings for risky configurations
        if safety_config.get("dry_run") is False and safety_config.get("auto_approve_bash"):
            issues.append("Warning: auto_approve_bash is enabled without dry_run. This could be dangerous.")
        
        if safety_config.get("auto_approve_write") is True:
            issues.append("Warning: auto_approve_write is enabled. Files may be modified without confirmation.")
        
        sandbox_timeout = safety_config.get("sandbox_timeout", 120)
        if sandbox_timeout < 1:
            issues.append("Error: sandbox_timeout must be at least 1 second.")
            return False, issues
        
        if sandbox_timeout > 3600:
            issues.append("Warning: sandbox_timeout is very high (>1 hour). Consider reducing for security.")
        
        return True, issues

    @staticmethod
    def test_configuration(config_dict: Dict) -> Dict[str, any]:
        """Run comprehensive configuration tests.

        Args:
            config_dict: Configuration to test

        Returns:
            Dictionary with test results
        """
        results = {
            "overall_valid": True,
            "tests": {}
        }
        
        # Test 1: Basic schema validation
        is_valid, errors = ConfigValidator.validate_config(config_dict)
        results["tests"]["schema_validation"] = {
            "valid": is_valid,
            "errors": errors
        }
        if not is_valid:
            results["overall_valid"] = False
        
        # Test 2: Provider validation
        model_config = config_dict.get("model", {})
        provider = model_config.get("provider", "anthropic")
        api_key = config_dict.get("api_key")
        
        is_valid, message = ConfigValidator.validate_provider_config(provider, api_key)
        results["tests"]["provider_validation"] = {
            "valid": is_valid,
            "message": message
        }
        
        # Test 3: Model validation
        model = model_config.get("model", "")
        is_valid, message = ConfigValidator.validate_model_config(model, provider)
        results["tests"]["model_validation"] = {
            "valid": is_valid,
            "message": message
        }
        
        # Test 4: Safety configuration
        safety_config = config_dict.get("safety", {})
        is_valid, issues = ConfigValidator.validate_safety_config(safety_config)
        results["tests"]["safety_validation"] = {
            "valid": is_valid,
            "warnings_errors": issues
        }
        
        return results
