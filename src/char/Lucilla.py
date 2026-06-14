import time

from src.char.BaseChar import BaseChar


class Lucilla(BaseChar):
    """Lucilla 自动战斗: 充能型 + 大招变身型角色。

    机制: 长按 E 或蓄力重击各攒 1 格能量, 攒满 3 格大招可用; 放大招后变身进入特殊形态
    (技能栏/大招图标消失, 视觉信号全失效), 固定时长输出后变回原建模, 再切人。
    详见架构文档第七节"踩坑与陷阱"。
    """

    # 单次长按/蓄力时长 (秒): 长按 E 或蓄力重击各攒 1 格能量
    HOLD_TIME: float = 1.2
    # 大招变身动画时长 (秒): 这段不可操作, 普攻无效, 先等过去
    LIBERATION_ANIMATION_TIME: float = 3.0
    # 变身后按住左键输出的时长 (秒): 变身期间无可靠 UI 信号标记结束, 只能用固定时长
    LIBERATION_HEAVY_TIME: float = 6.9
    # 攒能量阶段的整体上限 (秒), 防止攒不满时死循环
    CHARGE_TIME_OUT: float = 9.0
    # 能量已满但解放仍在 CD 时, CD 超过此秒数则放弃继续攒(避免溢出浪费), 直接切人
    LIBERATION_CD_SKIP: float = 1.5

    def do_perform(self):
        # perform_combat 放出大招时会自行切人并返回 True, 此处不再重复切人,
        # 避免大招后又回到 perform_combat 继续攒能量.
        if not self.perform_combat():
            self.switch_next_char()

    def perform_combat(self):
        """攒能量 -> 大招可用则放大招接输出.

        Returns:
            bool: 放出了大招(并已在 perform_liberation 内切人)返回 True, 否则 False.
        """
        start = time.time()

        # 0. 刚切回本角色时画面可能还在切人动画/恢复中, 技能栏 UI 未渲染稳定, 此时读 liberation
        #    高亮/CD 会得到陈旧值, 导致"上次满能量超时切人、切回后却不立即放招, 还得再攒2格".
        #    故先等 in_team 稳定 + 推进一帧刷新 CD 缓存, 给首次大招判断一个干净起点.
        self.task.wait_until(lambda: self.task.in_team()[0], time_out=2.0)
        self.task.next_frame()

        # 1. 一进来就可放大招 -> 放招并切人.
        if self.try_liberation():
            return True

        # 2. 攒能量循环. 每攒一格后立即重判大招是否就绪: 长按期间 CD 可能刚好走完、大招中途就绪,
        #    必须在循环内立即放招, 否则会出现"大招能放却没放、又攒一格/切人"的时序窗口 bug.
        while time.time() - start < self.CHARGE_TIME_OUT:
            self.charge_once()
            if self.try_liberation():
                return True
            # 能量已满但解放在较长 CD: 继续攒会溢出无用能量, 直接退出交给 do_perform 切人.
            if self.energy_full_but_lib_on_cd():
                self.logger.info('Lucilla energy full but liberation on cd, switch to save time')
                break

        return False

    def charge_once(self):
        """攒 1 格能量: E 可用优先长按 E, 否则蓄力重击.

        两者内部 sleep 均 check_combat=False, 不会被战斗误判打断; 且 sleep 已推进帧并重置
        scene.cd_refreshed, 故攒完后读 CD/能量即为最新值, 无需额外 next_frame().
        """
        if self.resonance_available():
            self.hold_resonance(self.HOLD_TIME)
        else:
            self.hold_heavy_attack(self.HOLD_TIME)

    def try_liberation(self):
        """大招就绪则放招(顺带先放声骸), 返回是否放出。"""
        if not self.liberation_available():
            return False
        if self.echo_available():
            self.click_echo(time_out=0)
        self.perform_liberation()
        self.switch_next_char()
        return True

    def energy_full_but_lib_on_cd(self):
        """解放能量已满(图标高亮)但仍在较长 CD 中, 返回 True 表示该切人而非继续攒能量。

        liberation_available() 把"能量满"和"无CD"绑在一起判断, 故用 check_cd=False 单看能量满.
        """
        energy_full = self.available('liberation', check_color=True, check_cd=False)
        return energy_full and self.task.get_cd('liberation') > self.LIBERATION_CD_SKIP

    def perform_liberation(self):
        """放大招进入变身形态, 按住左键固定时长输出后由调用方切人.

        不调用 BaseChar.click_liberation(): 它内部 ``while not in_team()`` 在变身形态下会因
        in_team 误判卡死到 7s 超时抛异常. 这里自己发解放键, 用 liberation_available() 变 False
        (大招图标消失 = 已进入形态) 作为放出信号.

        变身期间 in_team()/in_combat()/图标信号全部失效(详见架构文档 7.2), 故所有动作 check_combat
        =False + 底层 mouse_down/up, 且形态时长无可靠信号可测, 只能用固定时长.
        """
        if not self.task.use_liberation:
            return
        # 1. 点按解放键直到大招图标消失(已进入形态); 带 1.5s 上限防卡死.
        start = time.time()
        while self.liberation_available() and time.time() - start < 1.5:
            self.send_liberation_key()
            self.sleep(0.1, check_combat=False)
        self.record_liberation_use()
        self.logger.info('Lucilla perform lib')
        # 2. 先等过 ~3s 不可操作的变身动画(普攻无效), 再按住左键固定输出 ~7s.
        self.sleep(self.LIBERATION_ANIMATION_TIME, check_combat=False)
        self.task.mouse_down()
        try:
            self.sleep(self.LIBERATION_HEAVY_TIME, check_combat=False)
        finally:
            self.task.mouse_up()
        # 3. 输出结束后变身已结束、画面恢复, 等 in_team 稳定再让调用方切人, 给切人干净起点.
        self.task.wait_until(lambda: self.task.in_team()[0], time_out=3.0)
        self.logger.info('Lucilla perform lib end')

    def hold_heavy_attack(self, duration):
        """按住左键蓄力重击一段时间 (攒 1 格能量)。

        不用 BaseChar.heavy_attack: 它内部 sleep(duration) 默认带战斗检查, 蓄力期间某帧
        in_combat() 误判就抛 NotInCombatException 打断蓄力. 这里 check_combat=False 保证不被打断.
        """
        self.task.mouse_down()
        try:
            self.sleep(duration, check_combat=False)
        finally:
            self.task.mouse_up()

    def hold_resonance(self, duration):
        """长按共鸣技能键一段时间 (攒 1 格能量)。"""
        start = time.time()
        self.task.send_key_down(self.get_resonance_key())
        try:
            while time.time() - start < duration:
                self.check_combat()
                self.task.next_frame()
        finally:
            self.task.send_key_up(self.get_resonance_key())
        self.record_resonance_use()
