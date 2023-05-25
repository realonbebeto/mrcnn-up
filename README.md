
# Mask R-CNN Tensorflow 2.11

Migration of [matterpot](https://github.com/matterport/Mask_RCNN) Mask R-CNN implementation to work in versions starting with Tensorflow 2.11 leaf from [Renê Michel](https://github.com/Rene-Michel99/Mask-RCNN-TF2.8)



# Read More
Full documentation: [README](https://github.com/Rene-Michel99/Mask-RCNN-TF2.8#readme)
When running model use coco, imagenet, or last in the init_with argument
```code
  model.load_weights(init_with='coco', filepath=weights_path, by_name=True)
```
