from abc import ABC, abstractmethod

from lwe.core.config import Config
from lwe.core.logger import Logger
from lwe.core.template import TemplateManager
from lwe.core.preset_manager import PresetManager
from lwe.core import util

class Backend(ABC):
    """
    Base class/interface for all backends. This class provides a number of methods
    that must be implemented by any backend that extends this class.
    """

    def __init__(self, config=None):
        """
        Initializes the Backend instance.

        This method sets up attributes that should only be initialized once.

        :param config: Optional configuration for the backend. If not provided, it uses a default configuration.
        """
        self.conversation_id = None
        self.conversation_title = None
        self.conversation_title_set = None
        self.stream = False

    def initialize_backend(self, config=None):
        """
        Initializes the backend with provided or default configuration,
        and sets up necessary attributes.

        This method is safe to call for dynamically reloading backends.

        :param config: Optional configuration for the backend. If not provided, it uses a default configuration.
        """
        self.config = config or Config()
        self.log = Logger(self.__class__.__name__, self.config)
        self.provider_name = None
        self.provider = None
        self.message_clipboard = None
        self.streaming = False
        self.template_manager = TemplateManager(self.config)
        self.preset_manager = PresetManager(self.config)

    def set_available_models(self):
        """
        Sets the available models for the provider.
        """
        self.available_models = self.provider.available_models

    def make_llm(self, customizations=None):
        """
        Creates a Language Model (llm) using the provider.

        :param customizations: Optional dictionary for customizations.
        :return: Language Model (llm) object.
        """
        customizations = customizations or {}
        llm = self.provider.make_llm(customizations)
        return llm

    def set_model(self, model_name):
        """
        Sets the model for the provider.

        :param model_name: Name of the model to be set.
        :return: The result of the providers' set_model() method.
        """
        self.model = model_name
        return self.provider.set_model(model_name)

    def new_conversation(self):
        """
        Resets all attributes related to a conversation, such that a new
        conversation is started.
        """
        self.conversation_id = None
        self.conversation_title = None
        self.conversation_title_set = None
        self.message_clipboard = None

    def terminate_stream(self, _signal, _frame):
        """
        Handles termination signal and stops the stream if it's running.

        :param _signal: The signal that triggered the termination.
        :param _frame: Current stack frame.
        """
        self.log.info("Received signal to terminate stream")
        if self.streaming:
            self.streaming = False

    def get_runtime_config(self):
        """
        Retrieves the runtime configuration.

        :return: The runtime configuration as a string.
        """
        return ""

    def extract_preset_configuration_from_overrides(self, overrides):
        """
        Extracts preset configuration from the given overrides.

        :param overrides: The overrides from which to extract preset configuration.
        :return: A tuple containing a success indicator, preset configuration, and a user message.
        """
        preset_name = None
        preset_overrides = None
        self.log.debug(f"Extracting preset configuration from overrides: {overrides}")
        if 'request_overrides' in overrides and ('preset' in overrides['request_overrides'] or 'preset_overrides' in overrides['request_overrides']):
            if 'preset' in overrides['request_overrides']:
                preset_name = overrides['request_overrides']['preset']
                self.log.debug(f"Preset extracted from overrides: {preset_name}")
                if 'activate_preset' in overrides['request_overrides'] and overrides['request_overrides']['activate_preset']:
                    # TODO: activate_preset() currently lives in the API backend, and so should not
                    # strictly be called here.
                    self.log.info(f"Activating preset from overrides: {preset_name}")
                    self.activate_preset(preset_name)
            else:
                preset_name = self.active_preset_name
            if not preset_name:
                return False, (preset_name, preset_overrides, overrides), "No active preset to override"
            if 'preset_overrides' in overrides['request_overrides']:
                preset_overrides = overrides['request_overrides']['preset_overrides']
        return True, (preset_name, preset_overrides, overrides), f"Extracted preset configuration from request overrides: {overrides}"

    def run_template_setup(self, template_name, substitutions=None):
        """
        Sets up the run of a template.

        :param template_name: Name of the template to run.
        :param substitutions: Optional dictionary of substitutions.
        :return: A tuple containing a success indicator, tuple of template setup data, and a user message.
        """
        self.log.info(f"Setting up run of template: {template_name}")
        substitutions = substitutions or {}
        message, overrides = self.template_manager.build_message_from_template(template_name, substitutions)
        preset_name = None
        success, response, user_message = self.extract_preset_configuration_from_overrides(overrides)
        if not success:
            return success, (message, preset_name, overrides), user_message
        preset_name, preset_overrides, overrides = response
        if preset_name:
            success, llm, user_message = self.set_override_llm(preset_name, preset_overrides)
            if success:
                self.log.info(f"Switching to preset '{preset_name}' for template: {template_name}")
            else:
                return success, llm, user_message
        return True, (message, overrides), f"Set up of template run complete: {template_name}"

    def run_template_compiled(self, message, overrides=None):
        """
        Runs the compiled template.

        :param message: The message to be sent.
        :param overrides: Optional dictionary of overrides.
        :return: The response tuple from LLM request.
        """
        overrides = overrides or {}
        self.log.info("Running template")
        response = self._ask(message, **overrides)
        return response

    def run_template(self, template_name, template_vars=None, overrides=None):
        """
        Runs the given template with the provided variables and overrides.

        :param template_name: Name of the template to run.
        :param template_vars: Optional dictionary of template variables, will merged with any set in the template.
        :param overrides: Optional dictionary of overrides, will be merged with any set in the template.
        :return: The response tuple from the template run.
        """
        template_vars = template_vars or {}
        overrides = overrides or {}
        success, response, user_message = self.template_manager.get_template_variables_substitutions(template_name)
        if not success:
            return success, response, user_message
        _template, _variables, substitutions = response
        util.merge_dicts(substitutions, template_vars)
        success, response, user_message = self.run_template_setup(template_name, substitutions)
        if not success:
            return success, response, user_message
        message, template_overrides = response
        util.merge_dicts(template_overrides, overrides)
        response = self.run_template_compiled(message, template_overrides)
        return response

    @abstractmethod
    def conversation_data_to_messages(self, conversation_data):
        """
        Converts conversation data to messages. Must be implemented by the child class.

        :param conversation_data: Data of the conversation to be converted.
        """
        pass

    @abstractmethod
    def delete_conversation(self, uuid=None):
        """
        Deletes a conversation. Must be implemented by the child class.

        :param uuid: Optional unique identifier of the conversation.
        """
        pass

    @abstractmethod
    def set_title(self, title, conversation_id=None):
        """
        Sets the title of a conversation. Must be implemented by the child class.

        :param title: The title to be set.
        :param conversation_id: Optional ID of the conversation.
        """
        pass

    @abstractmethod
    def get_history(self, limit=20, offset=0):
        """
        Retrieves conversation history. Must be implemented by the child class.

        :param limit: Maximum number of history entries to retrieve.
        :param offset: Number of entries to skip from the start.
        """
        pass

    @abstractmethod
    def get_conversation(self, uuid=None):
        """
        Retrieves a conversation. Must be implemented by the child class.

        :param uuid: Optional unique identifier of the conversation.
        """
        pass

    @abstractmethod
    def set_override_llm(self, preset_name=None, preset_overrides=None):
        """
        Sets an override Language Model (llm) for the request. Must be implemented by the child class.

        :param preset_name: Optional name of the preset.
        :param preset_overrides: Optional dictionary of preset overrides.
        """
        pass

    @abstractmethod
    def ask_stream(self, input: str, request_overrides: dict):
        """
        Ask the LLM a question and stream a response.

        :param input: The input to be sent to the LLM.
        :type input: str
        :request_overrides: Overrides for this specific request.
        :type request_overrides: dict, optional
        :returns: success, LLM response, message
        :rtype: tuple
        """
        pass

    @abstractmethod
    def ask(self, input: str, request_overrides: dict):
        """
        Get a response from the LLM (non-streaming). Must be implemented by the child class.

        :param input: The input to be sent to the LLM.
        :type input: str
        :request_overrides: Overrides for this specific request.
        :type request_overrides: dict, optional
        :returns: success, LLM response, message
        :rtype: tuple
        """
        pass
