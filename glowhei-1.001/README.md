# 星霞黑体 GlowHei

屏幕显示用的中文黑体，与星霞宋体成对。字符集到 GBK（CP936）级，另附 CJK 扩展 A
的缺字回退。每个字符集有比例与等宽两款，字形与度量完全相同，区别只在
`isFixedPitch` 与 PANOSE 两个标志位。ASCII 一律半角（0.5 em），全角一律 1 em，
字体只有这两种宽度。

行距 1.141 em，与星霞宋体相同，两款可以混排或互换。

## 这里有什么

| 文件 | 内容 |
|---|---|
| `GlowHei-GBK.ttc` | GBK 全集，比例与等宽两款 |
| `GlowHei-GB2312.ttc` | GB2312 级，两款 |
| `GlowHei-ExtA.ttf` | CJK 扩展 A 的缺字回退 |
| `src/` | 构建管线的全部源码 |

整份为 SIL OFL 1.1，全文见 `LICENSE`，上游声明也在其中。

字体的 `fpgm` 表内含 Chlorophytum 的运行时函数库，该工具为 MIT，全文见
`LICENSE-MIT`。

fontconfig 配置 `65-glowhei.conf` 与自查脚本 `check-fontconfig.sh` 不在这份下载
里，它们在发布页的根目录——装不装字体都用得上，因此没有跟着字体走。

## 装上

Linux：

```sh
mkdir -p ~/.local/share/fonts
cp GlowHei-GBK.ttc GlowHei-ExtA.ttf ~/.local/share/fonts/
fc-cache -f
```

`fc-cache -f` 不能省。配套的 fontconfig 配置用一条扫描期规则修正等宽分类，而字体
列表建自扫描缓存，不重建缓存的话旧判定会留着。

Windows：右键安装 TTC。

## hinting 与体积

成品带 hinting，占体积的将近一半。汉字、假名走 Chlorophytum，拉丁、希腊、西里尔走
ttfautohint——两套工具各自处理它有模型的那部分，在各自的字体里生成，最后按码点合并。

收益集中在 12px 及以下的密集汉字：横画不粘连、笔画数可辨。判据是形状忠实度而非
对比度——以同一轮廓在高分辨率下渲染再降采样为基准，Chlorophytum 是各方案中最忠实
的一个，且比不加 hinting 更忠实。

只在大字号用字、或要把字体嵌进文档而在意体积，可以去掉：

```sh
make -C ../sbitgraft-1.001
../sbitgraft-1.001/sbitgraft --strip GlowHei-GBK.ttc GlowHei-GBK-nohint.ttc
```

轮廓、度量、字符映射与命名都不会被改动。

## 字形来源

轮廓取自 Sarasa Fixed SC（更纱黑体），其汉字来自思源黑体、拉丁来自 Iosevka。本项目
做的是子集化到 CP936、upem 降到 256、逐字择优简化、把超出行框的字形缩回去，以及重新
度量、命名与 hinting。

`src/` 下是完整的构建管线。简化一步需要 FontForge，以及本机的 `freetype-py` 与
`Pillow`——后两者用来渲染比对每个字形简化前后的形状，偏差超过 1% 的拟合一律退回。
这个检查不是可选项：缺了其中任何一个，整步跳过而不是放宽标准。hinting 需要
Chlorophytum 与 ttfautohint。两步都缺只是产物更大，构建不会中断。

## 项目

源码与问题跟踪：<https://github.com/lunzima/GlowHei>
联系：lunzima@lunzima.net
