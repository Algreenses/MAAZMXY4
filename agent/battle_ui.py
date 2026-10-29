"""Compose native recognition with an explicit absence guard on the same frame."""
from maa.custom_recognition import CustomRecognition


class NoRevivePrompt(CustomRecognition):
    def analyze(self, context, argv):
        # Maa's And node references reuse algorithm parameters, not the
        # referenced node's top-level inverse flag. Keep the native matcher
        # and negate its positive result here; missing resources fail closed.
        try:
            prompt = context.run_recognition('Result_RevivePrompt', argv.image)
        except Exception as error:
            print('复活提示检查失败，禁止返回地图：' + str(error), flush=True)
            return None
        if prompt is None or prompt.hit:
            return None
        return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail={'revival_prompt': False})
