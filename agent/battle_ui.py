"""Block native actions behind known modal skins on the same frame."""
from maa.custom_recognition import CustomRecognition


class NoRevivePrompt(CustomRecognition):
    def analyze(self, context, argv):
        # Maa's And node references reuse algorithm parameters, not the
        # referenced node's top-level inverse flag. Keep the native matcher
        # and negate its positive result here; missing resources fail closed.
        for name in ('Result_RevivePrompt', 'Nav_CommonConfirm'):
            try:
                prompt = context.run_recognition(name, argv.image)
            except Exception as error:
                print('浮窗检查失败，禁止操作底层界面：' + str(error), flush=True)
                return None
            if prompt is None or prompt.hit:
                return None
        return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1),
            detail={'revival_prompt': False, 'common_confirmation': False})
