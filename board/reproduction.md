# Board reproduction and recovery

本板运行前置条件：匹配的PetaLinux2020.2根文件系统、原BOOT.BIN与boot.scr、BCI FIT，设备树bci,platform-id=ps-stream-bci1，LCD标记emio-st7789-v1，旋钮标记emio-ec11-v1。自研IP基地址0x43d00000，FIFO0x43d10000；不得将原厂PWM地址当成BCI寄存器。

完整系统恢复以保留的原TF卡或整卡镜像为准。应用级恢复需保留Linux权限和符号链接。已部署应用位于/root/bci/final，服务为/etc/init.d/bci-ui，日志位于/root/bci/ui_runs。初次安装与安全关机更新不应重复运行或覆盖备份。

当前发布目录不含厂商Linux镜像。BOOT.BIN、boot.scr与image.ub的匹配备份留在项目私有归档和原TF卡。不要改写QSPI或启动配置来“试试看”；NPU项目使用另一张TF卡。

菜单：旋转选模式，短按进入，长按返回；安全关机需二次确认，默认为取消。存储保护检查通过后显示可以断电。实物首次关机及再次启动验收结果应附实际日志，不能用界面预览代替。
